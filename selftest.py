"""End-to-end self-test for RoomWatch. No camera and no internet needed.

Builds a synthetic 'room' video (a door on the left, people walking in and out),
enrols two of the three faces, then runs the real roomwatch pipeline over it
against local mock ntfy and webhook servers and asserts the alerts that come out.

    python selftest.py

Exit code 0 means every stage passed.
"""

from __future__ import annotations

import base64
import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import quietcv  # noqa: F401  -- must precede `import cv2`
import cv2
import numpy as np

import config as config_mod
import notifiers

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "_selftest")
ASSETS = os.path.join(HERE, "selftest_assets")
VIDEO = os.path.join(WORK, "room_scene.mp4")
DB_PATH = os.path.join(WORK, "faces", "people.json")
SRC = {
    "Alice": os.path.join(ASSETS, "Alice", "Alice_01.jpg"),
    "Bob": os.path.join(ASSETS, "Bob", "Bob_01.jpg"),
    "Stranger": os.path.join(ASSETS, "Stranger", "Stranger_01.jpg"),
}
# A second, different photo of Alice. Held as its own path rather than derived by
# string surgery on SRC["Alice"]: a folder anywhere in the path containing "01"
# (a date, rz f401n, Room01) would silently corrupt the result.
ALICE_ALT = os.path.join(ASSETS, "Alice", "Alice_02.jpg")
REQUIRED = [*SRC.values(), ALICE_ALT]

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
_failures: list[str] = []
_checks = 0


def check(label: str, ok: bool, detail: str = "") -> bool:
    global _checks
    _checks += 1
    print(f"  [{PASS if ok else FAIL}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        _failures.append(label)
    return ok


# ------------------------------------------------------- mock push servers

RECEIVED: list[dict] = []


class MockPushServer(BaseHTTPRequestHandler):
    """Records every POST so the self-test can inspect what the phone would get.

    Handles the three shapes the channels actually use: multipart (webhook),
    a raw image body with the caption in the Title header (ntfy photo), and
    plain text (ntfy text).

    It also copies ntfy's routing rule, because a permissive mock hides real
    bugs: ntfy answers 404 "page not found" if you put the attachment filename
    in the URL path instead of the Filename header, and a mock that always
    returns 200 will happily pass a build that can never deliver a photo.
    """
    def do_POST(self):  # noqa: N802
        # ntfy publishes to /<topic> and nothing deeper. Only apply that to
        # ntfy-shaped requests: the webhook legitimately posts multipart to a
        # deeper path, and 404-ing it would be a false alarm.
        ctype = self.headers.get("Content-Type", "")
        ntfy_style = "multipart" not in ctype and any(
            h in self.headers for h in ("Filename", "Tags", "Priority"))
        segments = [s for s in self.path.split("?")[0].split("/") if s]
        if ntfy_style and len(segments) != 1:
            payload = json.dumps(
                {"code": 40401, "http": 404, "error": "page not found"}).encode()
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        record: dict = {
            "method": self.path.rsplit("/", 1)[-1],
            "channel": "webhook" if "multipart" in ctype else "ntfy",
            "bytes": len(body),
            # Stored verbatim, exactly as ntfy does: no unquoting, so a
            # percent-encoded title shows up here and fails the check.
            "headers": {k.lower(): v for k, v in self.headers.items()
                        if k.lower() in ("title", "filename", "tags", "priority", "authorization")},
        }
        photo: bytes | None = None
        if "multipart" in ctype:
            fields = _parse_multipart(body, ctype)
            record["field_names"] = sorted(fields)
            record["caption"] = _first_text(fields, ("caption", "content", "text", "message"))
            photo = _find_jpeg(fields)
        elif "json" in ctype:
            try:
                record["text"] = json.loads(body).get("text", "")
            except (json.JSONDecodeError, UnicodeDecodeError):
                record["text"] = ""
        elif ctype.startswith("text/"):
            record["text"] = body.decode("utf-8", "replace")[:500]
        elif ctype.startswith("image/") or body[:2] == b"\xff\xd8":
            photo = body  # ntfy uploads the raw JPEG, not a multipart form
        if photo:
            record["photo_bytes"] = len(photo)
            out = os.path.join(WORK, f"received_{len(RECEIVED)}.jpg")
            with open(out, "wb") as fh:
                fh.write(photo)
            record["photo_path"] = out
            img = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
            record["photo_decodes"] = img is not None
            record["photo_size"] = None if img is None else img.shape[:2]
        RECEIVED.append(record)
        payload = json.dumps({"ok": True, "result": {"message_id": len(RECEIVED)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_a):
        pass


class MockDenyServer(BaseHTTPRequestHandler):
    """A self-hosted ntfy that refuses unauthenticated posts.

    This is what `auth-default-access: deny-all` actually does, and it is the
    behaviour the public ntfy.sh never shows -- so it needs its own mock. A
    permissive recorder would let a build with no credentials at all pass,
    right up until the day it pointed at a private server and every alert
    came back 403.
    """
    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        if not self.headers.get("Authorization"):
            payload = json.dumps(
                {"code": 40301, "http": 403, "error": "unauthorized"}).encode()
            self.send_response(403)
        else:
            payload = json.dumps({"ok": True, "result": {"message_id": 1}}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_a):
        pass


def _parse_multipart(body: bytes, ctype: str) -> dict[str, bytes]:
    """Parse any multipart/form-data body into {field name: raw bytes}.

    Uses the boundary from the header and does not care what the fields are
    called, so the same recorder works for a webhook (field "file"/"content"),
    the webhook ("file"/"content") and anything else.
    """
    m = re.search(r'boundary="?([^";]+)"?', ctype or "")
    if not m:
        return {}
    boundary = m.group(1).strip().encode()
    fields: dict[str, bytes] = {}
    # The delimiter in the body is "--" + boundary, not boundary on its own.
    delim = b"\r\n--" + boundary
    # Prefix a CRLF so the opening delimiter looks like every other one.
    for chunk in (b"\r\n" + body).split(delim)[1:]:
        if chunk.startswith(b"--"):
            break  # closing boundary
        head, sep, data = chunk.lstrip(b"\r\n").partition(b"\r\n\r\n")
        if not sep:
            continue
        nm = re.search(rb'name="([^"]*)"', head)
        if nm:
            fields[nm.group(1).decode("utf-8", "replace")] = data
    return fields


def _first_text(fields: dict[str, bytes], names: tuple[str, ...]) -> str:
    for name in names:
        if name in fields:
            return fields[name].decode("utf-8", "replace")
    return ""


def _find_jpeg(fields: dict[str, bytes]) -> bytes | None:
    for data in fields.values():
        if data[:2] == b"\xff\xd8":  # JPEG start-of-image marker
            return data
    return None


# ------------------------------------------------------------ scene building


def _synthetic_room(w: int, h: int) -> np.ndarray:
    """A plain lit room with a door frame on the left, so it looks like a doorway."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (168, 162, 155)  # BGR wall
    img[:, : w // 3] = (150, 145, 140)  # slightly darker left wall
    cv2.rectangle(img, (40, 30), (w // 3 - 30, h - 20), (205, 200, 195), -1)  # door frame
    cv2.rectangle(img, (40, 30), (w // 3 - 30, h - 20), (90, 88, 86), 3)
    cv2.circle(img, (w // 3 - 55, h // 2), 7, (120, 120, 130), -1)  # door handle
    cv2.rectangle(img, (0, h - 40), (w, h), (120, 116, 112), -1)  # skirting/floor edge
    return cv2.GaussianBlur(img, (5, 5), 0)


def _largest_face_crop(path: str) -> tuple[np.ndarray, tuple[int, int, int, int]] | None:
    """Detect the biggest face in a photo and return a padded square crop."""
    from faceengine import FaceEngine  # noqa: PLC0415

    global _ENGINE
    if _ENGINE is None:
        _ENGINE = FaceEngine(0.6)
    img = cv2.imread(path)
    if img is None:
        return None
    faces = _ENGINE.detect_faces(img)
    if not faces:
        return None
    f = max(faces, key=lambda r: float(r[2]) * float(r[3]))
    x, y, fw, fh = _ENGINE.box(f)
    side = int(max(fw, fh) * 2.6)
    cx, cy = x + fw // 2, y + fh // 2
    x0, y0 = max(0, cx - side // 2), max(0, cy - side // 2)
    x1, y1 = min(img.shape[1], x0 + side), min(img.shape[0], y0 + side)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return None
    crop = cv2.resize(crop, (220, 220), interpolation=cv2.INTER_LINEAR)
    return crop, (crop.shape[1], crop.shape[0])


_ENGINE = None


def build_video(source_faces: dict[str, str], out_path: str, fps: int = 12) -> None:
    """Script: empty -> A -> empty -> B -> empty -> stranger -> empty -> A+B."""
    W, H = 640, 360
    room = _synthetic_room(W, H)

    crops: dict[str, np.ndarray] = {}
    for name, path in source_faces.items():
        got = _largest_face_crop(path)
        if got:
            crops[name] = got[0]
    missing = [n for n in source_faces if n not in crops]
    if missing:
        raise RuntimeError(f"no face could be detected in: {missing}")

    # (label, [ (who, x_centre, y_centre) ], seconds)
    script = [
        ("empty", [], 2.0),
        ("Alice alone", [("Alice", 150, 190)], 3.0),
        ("empty", [], 1.5),
        ("Bob alone", [("Bob", 150, 190)], 3.0),
        ("empty", [], 1.5),
        ("stranger alone", [("Stranger", 150, 190)], 3.0),
        ("empty", [], 1.5),
        ("Alice and Bob", [("Alice", 120, 190), ("Bob", 300, 185)], 3.5),
    ]

    writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    frame_no = 0
    for label, placements, seconds in script:
        for _ in range(int(seconds * fps)):
            frame = room.copy()
            for who, cx, cy in placements:
                patch = crops[who]
                ph, pw = patch.shape[:2]
                # Gentle bob so tracking has to actually do some work.
                bob = int(6 * np.sin(frame_no / 6.0))
                x0, y0 = cx - pw // 2, cy - ph // 2 + bob
                mask = np.zeros((ph, pw), np.uint8)
                cv2.ellipse(mask, (pw // 2, ph // 2), (pw // 2 - 3, ph // 2 - 3), 0, 0, 360, 255, -1)
                m3 = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                region = (slice(y0, y0 + ph), slice(x0, x0 + pw))
                # Face inside the ellipse, room outside it.
                frame[region] = cv2.bitwise_and(frame[region], 255 - m3)
                frame[region] |= cv2.bitwise_and(patch, m3)
            writer.write(frame)
            frame_no += 1
    writer.release()


# ----------------------------------------------------------------- test steps


def main() -> int:
    missing = [p for p in REQUIRED if not os.path.exists(p)]
    if missing:
        raise SystemExit(f"selftest assets missing: {missing}")

    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(os.path.join(WORK, "snapshots"))
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    env = dict(os.environ, PYTHONUNBUFFERED="1")
    py = sys.executable

    print("\n=== 1. unit checks on the pure logic ===")
    sys.path.insert(0, HERE)
    from facedb import UNKNOWN, FaceDatabase
    from presence import iou, RoomPresence

    check("iou overlap", abs(iou((0, 0, 10, 10), (0, 0, 10, 10)) - 1.0) < 1e-6)
    check("iou disjoint", iou((0, 0, 10, 10), (50, 50, 10, 10)) == 0.0)

    db = FaceDatabase(DB_PATH, threshold=0.363)
    rng = np.random.default_rng(7)
    base = rng.normal(size=128).astype(np.float32)
    base /= np.linalg.norm(base)
    for _ in range(3):
        db.add("Alice", (base + rng.normal(scale=0.02, size=128)).astype(np.float32))
    other = rng.normal(size=128).astype(np.float32)
    other /= np.linalg.norm(other)
    db.add("Bob", other)
    db.save()

    check("same face matches its owner", db.match(base)[0] == "Alice",
          f"{db.match(base)[0]} @ {db.match(base)[1]:.3f}")
    check("a different face does not match Alice", db.match(other)[0] == "Bob",
          f"{db.match(other)[0]} @ {db.match(other)[1]:.3f}")
    check("noise is below threshold", db.match((base + rng.normal(scale=0.6, size=128).astype(np.float32)))[0] == UNKNOWN)

    reloaded = FaceDatabase(DB_PATH, threshold=0.363)
    check("database survives a reload", reloaded.names == ["Alice", "Bob"], str(reloaded.names))

    print("\n=== 1b. notifier formatting and channels ===")
    from notifiers import ConsoleNotifier, NtfyNotifier, WebhookNotifier, plain

    check("HTML is stripped for plain-text channels",
          plain("<b>Alice is in your room</b>\n13:16 &mdash; currently: Alice.") ==
          "Alice is in your room\n13:16 - currently: Alice.",
          repr(plain("<b>Alice is in your room</b>\n13:16 &mdash; currently: Alice.")))

    from notifiers import _is_configured
    check("placeholder values count as unconfigured",
          not _is_configured("PASTE_YOUR_SECRET_TOPIC_HERE") and not _is_configured(""),
          "")
    check("a real-looking topic counts as configured",
          _is_configured("room-k3f9x2mq7dp1"), "")

    check("ntfy stays disabled until a topic is set",
          not NtfyNotifier(server="https://ntfy.sh", topic="").ready
          and not NtfyNotifier(server="https://ntfy.sh",
                               topic="PASTE_YOUR_SECRET_TOPIC_HERE").ready
          and NtfyNotifier(server="https://ntfy.sh", topic="room-x").ready, "")
    check("webhook stays disabled until a real http url is set",
          not WebhookNotifier(url="").ready
          and not WebhookNotifier(url="PASTE_YOUR_WEBHOOK_URL_HERE").ready
          and WebhookNotifier(url="https://example.invalid/hook").ready, "")

    console_dir = os.path.join(WORK, "console_snaps")
    os.makedirs(console_dir, exist_ok=True)
    console = ConsoleNotifier(enabled=True, snapshot_dir=console_dir)
    jpeg = cv2.imencode(".jpg", np.full((120, 160, 3), 90, np.uint8))[1].tobytes()
    check("console notifier saves into its configured directory",
          console.send("Test &mdash; heading", "body line", jpeg)
          and len(glob.glob(os.path.join(console_dir, "console_*.jpg"))) == 1,
          console_dir)

    # --- presence state machine, driven by synthetic tracks -----------------
    def make(pres, entries, clock, dt=0.2):
        clock += dt
        dets = []
        for x, name, emb in entries:
            dets.append({"box": (x, 100, 80, 80), "centroid": (x + 40, 140),
                         "embedding": emb, "name": name, "score": 0.9, "quality": 0.8})
        events, _ = pres.update(dets, now=clock)
        return clock, events

    alice, bob = (100, "Alice", base), (300, "Bob", other)
    stranger = (100, UNKNOWN, rng.normal(size=128).astype(np.float32) / np.sqrt(128))

    pres = RoomPresence(grace_seconds=2, track_max_age=1, unknown_cooldown=0)
    clock = 1000.0
    for _ in range(8):
        clock, _ = make(pres, [], clock)

    ev = []
    for _ in range(12):
        clock, e = make(pres, [alice], clock)
        ev.extend(e)
    kinds = [e.kind for e in ev]
    check("one enter event, not a stream", kinds.count("enter") == 1, f"kinds={kinds}")
    check("enter names the person", any("Alice" in e.headline for e in ev), str([e.headline for e in ev]))
    check("enter wants a photo", all(e.photo_wanted for e in ev if e.kind == "enter"))
    check("Alice is counted as present", "Alice" in pres.occupancy(), str(pres.occupancy()))

    ev = []
    for _ in range(20):
        clock, e = make(pres, [], clock)
        ev.extend(e)
    check("a departure is reported exactly once",
          sum(e.kind == "leave" for e in ev) == 1 and any("Alice" in e.headline for e in ev),
          str([e.headline for e in ev]))
    check("leave does not need a photo", all(not e.photo_wanted for e in ev if e.kind == "leave"))
    check("room reads empty afterwards", pres.occupancy() == [], str(pres.occupancy()))

    pres2 = RoomPresence(grace_seconds=1, track_max_age=1, unknown_cooldown=0)
    ev = []
    for _ in range(16):
        clock, e = make(pres2, [alice, bob], clock)
        ev.extend(e)
    check("two people tracked at once", pres2.occupancy() == ["Alice", "Bob"], str(pres2.occupancy()))
    check("each got their own enter", sum(e.kind == "enter" for e in ev) == 2, str([e.headline for e in ev]))

    pres3 = RoomPresence(grace_seconds=1, track_max_age=1, unknown_cooldown=0)
    ev = []
    for _ in range(14):
        clock, e = make(pres3, [stranger], clock)
        ev.extend(e)
    check("a stranger is reported once", sum(e.kind == "unknown" for e in ev) == 1,
          str([e.headline for e in ev]))
    check("stranger alert carries a photo", all(e.photo_wanted for e in ev if e.kind == "unknown"))

    # A stranger who leaves and comes back must not alert again inside the cooldown.
    pres4 = RoomPresence(grace_seconds=0.5, track_max_age=0.5, unknown_cooldown=999)
    ev = []
    for _ in range(12):
        clock, e = make(pres4, [stranger], clock)
        ev.extend(e)
    check("stranger alerts the first time", sum(e.kind == "unknown" for e in ev) == 1,
          str([e.headline for e in ev]))
    for _ in range(12):  # stranger leaves
        clock, e = make(pres4, [], clock)
    ev = []
    for _ in range(12):  # and comes straight back
        clock, e = make(pres4, [stranger], clock)
        ev.extend(e)
    check("unknown cooldown suppresses the return visit", sum(e.kind == "unknown" for e in ev) == 0,
          str([e.headline for e in ev]))

    pres5 = RoomPresence(grace_seconds=1, track_max_age=1, unknown_cooldown=0,
                         night_mode=(0, 23))  # effectively always "night"
    ev = []
    for _ in range(14):
        clock, e = make(pres5, [alice], clock)
        ev.extend(e)
    check("night mode silences known people", sum(e.kind == "enter" for e in ev) == 0,
          str([e.headline for e in ev]))

    pres6 = RoomPresence(grace_seconds=1, track_max_age=1, unknown_cooldown=0,
                         resend_while_present=999)
    ev = []
    for _ in range(14):
        clock, e = make(pres6, [alice], clock)
        ev.extend(e)
    check("resend_while_present does not re-alert on arrival", sum(e.kind == "enter" for e in ev) == 1,
          str([e.headline for e in ev]))

    print("\n=== 2. models + face engine on real photographs ===")
    from faceengine import FaceEngine

    engine = FaceEngine(0.6)
    check("YuNet detector loaded", engine.detector is not None)
    check("SFace recogniser loaded", engine.recognizer is not None)

    for label, path in SRC.items():
        img = cv2.imread(path)
        if img is None:
            check(f"{label} photo is readable", False, path)
            continue
        faces = engine.detect_faces(img)
        check(f"face detected in the {label} photo", len(faces) >= 1, f"{len(faces)} found")
        if faces:
            f = max(faces, key=lambda r: float(r[2]) * float(r[3]))
            emb = engine.embed(img, f)
            check(f"128-d embedding produced for {label}", emb is not None and emb.shape == (128,),
                  "None" if emb is None else str(emb.shape))

    print("\n=== 3. build a synthetic room video ===")
    build_video(SRC, VIDEO)
    cap = cv2.VideoCapture(VIDEO)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    check("video was written", os.path.getsize(VIDEO) > 20_000, f"{os.path.getsize(VIDEO) / 1024:.0f} KB")
    check("video is long enough for tracking to settle", total >= 150, f"{total} frames")

    print("\n=== 4. enrol two of the three faces via enroll.py ===")
    # enroll.py and roomwatch.py both read/write the real ./faces/people.json, so
    # stash whatever is there. The self-test must never damage a real enrolment,
    # and must also not leave a bogus Alice/Bob behind on a fresh install.
    real_db = os.path.join(HERE, "faces", "people.json")
    stash = os.path.join(WORK, "people.json.backup")
    had_real_db = os.path.exists(real_db)
    if had_real_db:
        shutil.copy(real_db, stash)

    try:
        for label in ("Alice", "Bob"):
            r = subprocess.run(
                [py, os.path.join(HERE, "enroll.py"), label,
                 "--from", os.path.join(ASSETS, label)],
                cwd=HERE, env=env, capture_output=True, text=True,
            )
            tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
            check(f"enroll.py succeeded for {label}", r.returncode == 0, f"rc={r.returncode} | {tail}")

        enrolled = FaceDatabase(real_db)
        added = [n for n in ("Alice", "Bob") if n in enrolled.names]
        check("both test people were enrolled", added == ["Alice", "Bob"], str(enrolled.names))
        check("the stranger was NOT enrolled", "Stranger" not in enrolled.names, str(enrolled.names))
        if had_real_db:
            check("a pre-existing enrolment was not clobbered",
                  all(n in enrolled.names for n in FaceDatabase(stash).names),
                  f"{FaceDatabase(stash).names} still present")

        def identify(path: str) -> tuple[str, float]:
            img = cv2.imread(path)
            if img is None:
                return "<could not read image>", 0.0
            found = engine.detect_faces(img)
            if not found:
                return "<no face found>", 0.0
            biggest = max(found, key=lambda r: float(r[2]) * float(r[3]))
            return enrolled.match(engine.embed(img, biggest))

        who, score = identify(SRC["Alice"])
        check("Alice's photo identifies as Alice", who == "Alice", f"{who} @ {score:.3f}")
        who, score = identify(ALICE_ALT)
        check("Alice's second, different photo also identifies", who == "Alice", f"{who} @ {score:.3f}")
        who, score = identify(SRC["Bob"])
        check("Bob's photo identifies as Bob", who == "Bob", f"{who} @ {score:.3f}")
        who, score = identify(SRC["Stranger"])
        check("the stranger falls through to Unknown", who == UNKNOWN, f"{who} @ {score:.3f}")

        print("\n=== 5. mock ntfy + webhook servers ===")
        srv = HTTPServer(("127.0.0.1", 0), MockPushServer)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        check("mock server listening", port > 0, f"127.0.0.1:{port}")

        # Both live channels are exercised through the real pipeline, so the
        # shipped config's own topic must not be used or the self-test would post
        # to ntfy.sh. Everything is redirected at the mock.
        cfg = config_mod.load(os.path.join(HERE, "config.json"))
        cfg["ntfy"] = {"enabled": True, "server": f"http://127.0.0.1:{port}",
                       "topic": "selftest_topic_abc123"}
        cfg["webhook"] = {"enabled": True,
                          "url": f"http://127.0.0.1:{port}/webhook/hook1",
                          "field_name": "file"}
        cfg["console"]["enabled"] = False
        cfg["camera"]["target_fps"] = 12
        cfg["detection"]["presence_grace_seconds"] = 0.6
        cfg["detection"]["track_max_age_seconds"] = 0.5
        cfg["alerts"]["unknown_cooldown_seconds"] = 2
        cfg["alerts"]["heartbeat_minutes"] = 0
        cfg["storage"]["snapshot_dir"] = os.path.join(WORK, "snapshots")
        cfg["runtime"]["startup_delay_seconds"] = 0
        test_cfg = os.path.join(WORK, "config.test.json")
        json.dump(cfg, open(test_cfg, "w"), indent=2)

        print("\n=== 6. run the real pipeline over that video ===")
        t0 = time.time()
        proc = subprocess.run(
            [py, os.path.join(HERE, "roomwatch.py"), "--config", test_cfg,
             "--source", VIDEO, "--fps", "12", "--no-loop"],
            cwd=HERE, env=env, capture_output=True, text=True, timeout=300,
        )
        elapsed = time.time() - t0
        log = proc.stdout + proc.stderr
        check("roomwatch.py exited cleanly", proc.returncode == 0, f"rc={proc.returncode}")
        if proc.returncode != 0:
            print(log[-3000:])
        tb = log.find("Traceback")
        check("no exception was raised", tb < 0, log[tb:tb + 500] if tb >= 0 else "")
        print(f"  ({elapsed:.1f}s wall clock for a {total}-frame replay)")

        for line in log.splitlines():
            if "RoomWatch started" in line or "stopped after" in line or "alive |" in line:
                print(f"    | {line.strip()}")
            elif any(k in line for k in ("enter ", "leave ", "unknown ")):
                print(f"    | {line.strip()}")
    finally:
        if had_real_db:
            shutil.copy(stash, real_db)
            print(f"\n  (restored your real face database -- "
                  f"{len(FaceDatabase(real_db))} person(s) intact)")
        elif os.path.exists(real_db):
            os.remove(real_db)
            print("\n  (removed the throwaway test database)")

    print("\n=== 7. did the phone get told? ===")

    def _caption_of(r: dict) -> str:
        """The caption reaches ntfy as a Title header and the webhook as a field.

        Deliberately not unquoted: ntfy stores the header verbatim, so a
        percent-encoded title would show up on the phone as %20 escapes.
        """
        return r.get("caption") or r.get("headers", {}).get("title", "")

    def is_ntfy(r: dict) -> bool:
        return r.get("channel") == "ntfy"

    photos_sent = [r for r in RECEIVED if r.get("photo_bytes")]
    texts = [r for r in RECEIVED if r.get("text")]
    captions = " || ".join(_caption_of(r) for r in photos_sent)
    for r in photos_sent:
        print(f"    > {r['method']}  {r['photo_bytes'] // 1024} KB  {r['photo_size']}  {_caption_of(r)[:80]!r}")
    for r in texts:
        print(f"    > {r['method']}  text  {r.get('text', '')[:90]!r}")

    all_texts = " || ".join(r.get("text", "") for r in texts)
    check("alerts reached the phone", len(RECEIVED) > 0, f"{len(RECEIVED)} HTTP posts")
    check("alerts carried a photo", len(photos_sent) >= 2, f"{len(photos_sent)} photo uploads")
    check("every photo decoded as a real JPEG", all(r.get("photo_decodes") for r in photos_sent))
    check("photos have sane dimensions",
          all(r["photo_size"] and r["photo_size"][0] >= 200 and r["photo_size"][1] >= 200 for r in photos_sent),
          str([r["photo_size"] for r in photos_sent]))
    check("every upload carried its caption", all(_caption_of(r) for r in photos_sent), captions[:160])
    check("departures arrived as text-only messages", len(texts) >= 1, f"{len(texts)} text messages")
    # ntfy and the console have no HTML renderer, so a raw "&mdash;" or "<b>"
    # reaching them would be shown to the user literally.
    check("plain-text messages contain no HTML tags or entities",
          "<b>" not in all_texts and "&mdash;" not in all_texts
          and "&amp;" not in all_texts and "&lt;" not in all_texts,
          all_texts[:200])
    check("Alice was named in an alert", "Alice" in captions, captions[:200])
    check("Bob was named in an alert", "Bob" in captions, captions[:200])
    # ntfy puts the headline in the photo's Title and the detail ("face not in
    # your enrolled list") in the follow-up text message, so search both.
    everything_said = captions + " " + all_texts
    check("the stranger was called out as not enrolled",
          "Unknown" in everything_said or "not in your enrolled list" in everything_said,
          everything_said[:240])

    # Both channels are live at once, so each alert must reach each of them.
    ntfy_photos = [r for r in photos_sent if is_ntfy(r)]
    hook_photos = [r for r in photos_sent if not is_ntfy(r)]
    all_titles = " || ".join(r.get("headers", {}).get("title", "") for r in RECEIVED)
    check("ntfy titles are sent verbatim, not percent-encoded",
          "%20" not in all_titles and "%3A" not in all_titles
          and "<b>" not in all_titles, all_titles[:200])

    check("the same alerts went out over ntfy and the webhook",
          len(ntfy_photos) == len(hook_photos) and len(ntfy_photos) >= 2,
          f"ntfy {len(ntfy_photos)} vs webhook {len(hook_photos)}")

    print("\n=== 8. snapshots on disk ===")
    enter_photos = [r for r in photos_sent
                    if is_ntfy(r) and "in your room" in _caption_of(r)]
    snaps = sorted(glob.glob(os.path.join(cfg["storage"]["snapshot_dir"], "*.jpg")))
    check("snapshots were saved", len(snaps) >= 2, f"{len(snaps)} files")
    # Every enter/unknown alert saves exactly one snapshot, so the file count and
    # the number of enter/unknown alert photos must agree.
    check("one snapshot file per enter/unknown alert", len(snaps) == len(enter_photos),
          f"{len(snaps)} files vs {len(enter_photos)} alert photos")
    if snaps:
        check("a saved snapshot opens", cv2.imread(snaps[0]) is not None, os.path.basename(snaps[0]))
        for r in enter_photos[:3]:
            if r.get("photo_path"):
                print(f"  sample: {r['photo_path']}")

    print("\n=== 9. test_phone.py end to end ===")
    RECEIVED.clear()
    phone_proc = subprocess.run(
        [py, os.path.join(HERE, "test_phone.py"), "--config", test_cfg],
        cwd=HERE, env=env, capture_output=True, text=True, timeout=120,
    )
    if phone_proc.returncode != 0:
        print((phone_proc.stdout + phone_proc.stderr)[-1200:])
    check("test_phone.py exited cleanly", phone_proc.returncode == 0, f"rc={phone_proc.returncode}")
    for line in phone_proc.stdout.splitlines():
        if line.strip().startswith(("sent", "FAILED", "Configured")):
            print(f"    | {line.strip()}")
    tp_photos = [r for r in RECEIVED if r.get("photo_bytes")]
    check("test_phone.py sent a photo on both channels", len(tp_photos) == 2, f"{len(tp_photos)} uploads")
    check("the test photo is a real JPEG on both channels",
          bool(tp_photos) and all(r.get("photo_decodes") for r in tp_photos),
          str([r.get("photo_size") for r in tp_photos]))
    check("the test photo carried its caption",
          bool(tp_photos) and all(_caption_of(r) for r in tp_photos),
          " || ".join(_caption_of(r) for r in tp_photos)[:160])
    check("test_phone.py sent a text line on both channels",
          len([r for r in RECEIVED if r.get("text")]) >= 2,
          f"{len([r for r in RECEIVED if r.get('text')])} texts")

    print("\n=== 10. self-hosted ntfy authentication ===")
    # The difference between "self-hosted" and "actually private" is this
    # header. ntfy.sh has no accounts, so the no-credentials path must keep
    # working unchanged; a deny-all server needs a real credential or it
    # answers 403 to every alert.
    RECEIVED.clear()
    AUTH_TOKEN = "tk_selftest_not_a_real_token_0123456789"
    AUTH_PW = "selftest-password-not-real"
    auth_base = {"server": f"http://127.0.0.1:{port}", "topic": "selftest_auth", "enabled": True}
    cases = {
        "open": {},
        "token": {"token": AUTH_TOKEN},
        "basic": {"username": "selftest", "password": AUTH_PW},
        "both": {"token": AUTH_TOKEN, "username": "selftest", "password": AUTH_PW},
        "placeholder token": {"token": "PASTE_YOUR_NTFY_TOKEN_HERE"},
    }
    sent_auth: dict[str, str] = {}
    for label, creds in cases.items():
        before = len(RECEIVED)
        got_ok = notifiers.NtfyNotifier(**auth_base, **creds).send_message("auth probe")
        posts = [r for r in RECEIVED[before:] if r.get("channel") == "ntfy"]
        sent_auth[label] = posts[0].get("headers", {}).get("authorization", "<none>") if posts else "<no post>"
        check(f"ntfy still posts with {label} credentials",
              got_ok and len(posts) == 1 and posts[0].get("text") == "auth probe",
              sent_auth[label][:48])

    check("an open server is sent no Authorization header at all",
          sent_auth["open"] == "<none>", sent_auth["open"])
    check("a token is sent as a bearer credential",
          sent_auth["token"] == f"Bearer {AUTH_TOKEN}", sent_auth["token"])
    basic_raw = sent_auth["basic"].split(" ", 1)[-1]
    try:
        decoded = base64.b64decode(basic_raw).decode("utf-8")
    except Exception:  # noqa: BLE001 -- the check reports the value either way
        decoded = "<not base64>"
    check("a username and password are sent as Basic, not in the clear",
          sent_auth["basic"].startswith("Basic ") and decoded == f"selftest:{AUTH_PW}",
          f"decoded to {decoded!r}")
    check("a token wins over a username, so a leftover username cannot weaken it",
          sent_auth["both"] == f"Bearer {AUTH_TOKEN}", sent_auth["both"])
    check("an unfilled token placeholder is treated as no token, not as one",
          sent_auth["placeholder token"] == "<none>", sent_auth["placeholder token"])

    check("auth_mode reports the mode, never the secret",
          notifiers.NtfyNotifier(**auth_base).auth_mode == "open"
          and notifiers.NtfyNotifier(**auth_base, token=AUTH_TOKEN).auth_mode == "token"
          and notifiers.NtfyNotifier(**auth_base, username="u", password="p").auth_mode == "basic",
          "open/token/basic")

    # The credential must travel in a header. In the URL it lands in the
    # server's access log and in any proxy log on the way.
    photo_auth: list[str] = []
    RECEIVED.clear()
    notifiers.NtfyNotifier(**auth_base, token=AUTH_TOKEN).send_photo(
        b"\xff\xd8\xff\xe0" + b"0" * 64 + b"\xff\xd9", "auth photo")
    photo_auth = [r.get("headers", {}).get("authorization", "<none>") for r in RECEIVED]
    check("the photo upload and its caption both carry the credential",
          len(photo_auth) == 2 and all(v == f"Bearer {AUTH_TOKEN}" for v in photo_auth),
          str([v[:24] for v in photo_auth]))
    paths = [r.get("method", "") for r in RECEIVED]
    check("the credential is never put in the URL",
          all("tk_" not in p and "@" not in p for p in paths), str(paths[:2]))

    # And a real deny-all server must accept the authenticated one and reject
    # the open one, with a log line that says which of the two to go and check.
    deny = HTTPServer(("127.0.0.1", 0), MockDenyServer)
    deny_port = deny.server_address[1]
    threading.Thread(target=deny.serve_forever, daemon=True).start()
    logged: list[str] = []

    class _Grab(logging.Handler):
        def emit(self, record):
            logged.append(record.getMessage())

    grab = _Grab()
    notifiers.log.addHandler(grab)
    prev_level = notifiers.log.level
    notifiers.log.setLevel(logging.WARNING)
    try:
        deny_base = {"server": f"http://127.0.0.1:{deny_port}", "topic": "selftest_auth",
                     "enabled": True}
        denied = notifiers.NtfyNotifier(**deny_base).send_message("no credentials")
        allowed = notifiers.NtfyNotifier(**deny_base, token=AUTH_TOKEN).send_message("with token")
    finally:
        notifiers.log.removeHandler(grab)
        notifiers.log.setLevel(prev_level)
        deny.shutdown()
    check("a deny-all server refuses an unauthenticated post and accepts the token",
          denied is False and allowed is True, f"open={denied} token={allowed}")
    check("the 403 is logged with the auth mode and topic, so it can be diagnosed",
          any("token" in m and "selftest_auth" in m for m in logged),
          " || ".join(logged)[:200])
    check("no credential is ever written to the log",
          not any(AUTH_TOKEN in m or AUTH_PW in m for m in logged),
          " || ".join(logged)[:200])

    srv.shutdown()

    print("\n=== 11. config guards ===")
    # config.json is gitignored because it holds a real ntfy topic, so a fresh
    # clone only has the tracked template. The self-test has to work either way.
    example_path = os.path.join(HERE, "config.example.json")
    mine_path = os.path.join(HERE, "config.json")
    example = config_mod.load(example_path)
    check("the tracked template still exists and is valid JSON",
          isinstance(example.get("ntfy"), dict), example_path)

    # Whatever the local config says, the self-test must not use its topic.
    test_cfg_text = open(test_cfg, encoding="utf-8").read()
    real_topic = (config_mod.load(mine_path)["ntfy"]["topic"]
                  if os.path.exists(mine_path) else None)
    check("the self-test never posts to the live config's real topic",
          (real_topic is None or real_topic not in test_cfg_text)
          and "selftest_topic_abc123" in test_cfg_text,
          f"live topic {real_topic!r} absent from the test config")

    # The template must ship unfilled, and must say so helpfully rather than
    # looking configured.
    tpl_problems = notifiers.channel_setup_problems(example)
    check("the tracked template ships with a placeholder topic, not a real one",
          example["ntfy"]["topic"].startswith("PASTE_"), example["ntfy"]["topic"])
    check("an unfilled ntfy topic is caught and explained",
          any("placeholder" in prob for prob in tpl_problems), str(tpl_problems))

    # A fresh clone must not ship credentials, and must not look configured
    # because someone left a placeholder token in the template.
    check("the tracked template ships with no ntfy credentials",
          all(not example["ntfy"].get(k) for k in ("token", "username", "password")),
          str({k: example["ntfy"].get(k) for k in ("token", "username", "password")}))
    half = {"ntfy": {"enabled": True, "topic": "room-x", "username": "me"}}
    check("a username with no password or token is caught and explained",
          any("password" in p for p in notifiers.channel_setup_problems(half)),
          str(notifiers.channel_setup_problems(half)))

    # config.json is where a real token or password lives, and this file is
    # tracked. Assert the two never overlap, so a credential can never be
    # committed by accident through a copy-paste into a test.
    if os.path.exists(mine_path):
        live_ntfy = config_mod.load(mine_path).get("ntfy") or {}
        selftest_src = open(os.path.join(HERE, "selftest.py"), encoding="utf-8").read()
        secrets_in_src = [k for k in ("token", "username", "password")
                          if notifiers._is_configured(live_ntfy.get(k))
                          and str(live_ntfy[k]) in selftest_src]
        check("no live ntfy credential is hard-coded in this tracked file",
              not secrets_in_src, f"leaked: {secrets_in_src}")

    for label, path in (("template", example_path), ("local config", mine_path)):
        if not os.path.exists(path):
            continue
        raw = json.load(open(path, encoding="utf-8"))
        check(f"the {label} has no telegram section at all",
              "telegram" not in raw and "telegram" not in config_mod.load(path),
              str([k for k in raw if "telegram" in k]))

    if real_topic and not real_topic.startswith("PASTE_"):
        mine = config_mod.load(mine_path)
        check("a filled local config reports no setup problems",
              not notifiers.channel_setup_problems(mine),
              str(notifiers.channel_setup_problems(mine)))
        check("a filled local config resolves to a working ntfy channel",
              [type(c).__name__ for c in notifiers.build_phone_channels(mine)] == ["NtfyNotifier"],
              str([type(c).__name__ for c in notifiers.build_phone_channels(mine)]))
    else:
        print("    (no filled config.json here -- that is expected on a fresh clone)")

    print()
    if _failures:
        print(f"{len(_failures)} CHECK(S) FAILED:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print(f"ALL {_checks} CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

