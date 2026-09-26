"""RoomWatch -- watch the laptop webcam, tell your phone who walked into the room.

    python roomwatch.py                      # live camera
    python roomwatch.py --source clip.mp4     # replay a video (used for testing)
    python roomwatch.py --source photos/      # replay a folder of images
    python roomwatch.py --check              # verify config + models + camera, then exit

Everything is local: frames are read, matched and discarded on the laptop. The
only thing that leaves the machine is the annotated snapshot attached to the
notification you asked for.
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import platform
import queue
import signal
import sys
import threading
import time

import quietcv  # noqa: F401  -- must precede `import cv2`
import cv2

import render
from config import base_dir, load
from faceengine import DetectionError, FaceEngine
from facedb import FaceDatabase
import notifiers
from notifiers import ConsoleNotifier
from presence import RoomPresence

log = logging.getLogger("roomwatch")

HERE = base_dir()
# Presence of this file mutes alerts. pause.bat creates it, resume.bat removes
# it, and the watcher picks it up on the next event without a restart.
PAUSE_FILE = os.path.join(HERE, "paused")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")
VIDEO_EXTS = (".mp4", ".avi", ".mkv", ".mov", ".m4v")


# --------------------------------------------------------------------- source


class _StillImage:
    """Presents one JPEG/PNG as a source that yields it a single time."""

    def __init__(self, img):
        self.img = img
        self._served = False

    def read(self):
        if self._served or self.img is None:
            return False, None
        self._served = True
        return True, self.img.copy()

    def isOpened(self):  # noqa: N802 - mirrors cv2.VideoCapture
        return self.img is not None

    def set(self, *_a, **_k):
        return True

    def get(self, *_a, **_k):
        return 0

    def release(self):
        pass


class FrameSource:
    """Camera or replay behind one interface, with camera-loss recovery."""

    def __init__(self, cfg: dict, source: str | None, loop: bool | None = None) -> None:
        self.cfg = cfg
        self.source = source
        self.is_live = source is None
        self.files: list[str] = []
        self.index = 0
        self.loop = False

        if source is None:
            return

        if os.path.isdir(source):
            found: list[str] = []
            for ext in IMAGE_EXTS + VIDEO_EXTS:
                found.extend(glob.glob(os.path.join(source, ext)))
            self.files = sorted(found)
            self.loop = True if loop is None else loop
        else:
            self.files = [source]
            self.loop = bool(loop)

        if not self.files:
            raise FileNotFoundError(f"no readable images or videos found at {source!r}")

    def open(self):
        """Return (capture, label). Live -> a VideoCapture; still image -> _StillImage."""
        if self.is_live:
            cam = self.cfg["camera"]
            index = int(cam["camera_index"])
            use_dshow = bool(cam["use_dshow"]) and platform.system() == "Windows"
            cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if use_dshow else cv2.VideoCapture(index)
            if not cap.isOpened() and use_dshow:
                log.warning("DirectShow backend failed for camera %s; trying default backend", index)
                cap = cv2.VideoCapture(index)
            if not cap.isOpened():
                raise RuntimeError(
                    f"Could not open camera index {index}.\n"
                    "  - Close Windows Camera, Teams and Zoom first; they hold the device open.\n"
                    "  - Settings > Privacy & security > Camera must be switched on.\n"
                    "  - Try another index:  --camera 1   or   --camera 2\n"
                    "  - Some HP lids have a physical camera-shutter slider; check it is open."
                )
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(cam["frame_width"]))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(cam["frame_height"]))
            cap.set(cv2.CAP_PROP_FPS, int(cam["target_fps"]))
            # Ask the driver for a real frame immediately so /check fails fast
            # on a black/occupied device rather than looking 'fine'.
            cap.grab()
            log.info(
                "camera %d open -> %.0fx%.0f @ %.1f fps",
                index,
                cap.get(cv2.CAP_PROP_FRAME_WIDTH),
                cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
                cap.get(cv2.CAP_PROP_FPS),
            )
            return cap, f"camera {index}"

        path = self.files[self.index % len(self.files)]
        self.index += 1
        if path.lower().endswith(IMAGE_EXTS):
            img = cv2.imread(path)
            if img is None:
                raise RuntimeError(f"could not decode image {path}")
            return _StillImage(img), os.path.basename(path)

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError(f"could not open video {path}")
        return cap, os.path.basename(path)

    def exhausted(self) -> bool:
        """True once a non-looping replay is spent. Live sources never are."""
        return not self.is_live and not self.loop and self.index >= len(self.files)


# ------------------------------------------------------------------- helpers


def prune_snapshots(directory: str, keep: int) -> None:
    if keep <= 0 or not os.path.isdir(directory):
        return
    files = sorted(glob.glob(os.path.join(directory, "*.jpg")), key=os.path.getmtime)
    for stale in files[:-keep]:
        try:
            os.remove(stale)
        except OSError:
            pass


def detect_and_label(engine: FaceEngine, db: FaceDatabase, frame) -> list[dict]:
    """One inference pass: detect, align, embed, match. Returns tracking dicts."""
    out: list[dict] = []
    for face in engine.detect_faces(frame):
        x, y, w, h = engine.box(face)
        if w < 24 or h < 24:
            continue  # too few pixels to identify honestly
        embedding = engine.embed(frame, face)
        if embedding is None:
            continue
        name, score = db.match(embedding)
        out.append(
            {
                "box": (x, y, w, h),
                "centroid": (x + w / 2.0, y + h / 2.0),
                "embedding": embedding,
                "name": name,
                "score": score,
                "quality": engine.quality(face),
            }
        )
    return out


# ----------------------------------------------------------------------- main


def _configure_logging(level_name: str, log_file: str | None) -> None:
    """Console + rotating file.

    The file half is not optional: when RoomWatch is started at logon with no
    visible window, roomwatch.log is the only way to find out what it is doing.
    """
    from logging.handlers import RotatingFileHandler

    level = getattr(logging, str(level_name).upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
            fileh = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3,
                                        encoding="utf-8")
            fileh.setFormatter(fmt)
            root.addHandler(fileh)
        except OSError as exc:
            root.warning("could not open log file %s (%s); continuing without it", log_file, exc)

    # RoomWatch's own failure messages, not a library's progress chatter.
    logging.getLogger("roomwatch.notify").setLevel(logging.WARNING)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RoomWatch -- notify your phone when someone enters the room",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=os.path.join(HERE, "config.json"))
    parser.add_argument("--source", help="replay a video, image or folder instead of the live camera")
    parser.add_argument("--loop", dest="loop", action="store_true", default=None,
                        help="loop a replay source (default: on for folders, off for single files)")
    parser.add_argument("--no-loop", dest="loop", action="store_false")
    parser.add_argument("--camera", type=int, help="override the camera index")
    parser.add_argument("--fps", type=float, help="override the analysis frame rate")
    parser.add_argument("--startup-delay", type=float,
                        help="seconds to wait before claiming the camera (config default otherwise)")
    parser.add_argument("--pause", action="store_true", help="start with phone alerts muted")
    parser.add_argument("--check", action="store_true", help="verify setup, then exit")
    args = parser.parse_args()

    cfg = load(args.config)
    if args.camera is not None:
        cfg["camera"]["camera_index"] = args.camera
    if args.fps is not None:
        cfg["camera"]["target_fps"] = args.fps
    if args.startup_delay is not None:
        cfg["runtime"]["startup_delay_seconds"] = max(0.0, args.startup_delay)

    _configure_logging(
        cfg["runtime"]["log_level"],
        os.path.join(HERE, cfg["runtime"]["log_file"]) if cfg["runtime"].get("log_file") else None,
    )

    # Resolve the replay path before chdir, or a relative one breaks.
    source = os.path.abspath(args.source) if args.source else None
    os.chdir(HERE)

    # --- engine + database -------------------------------------------------
    try:
        engine = FaceEngine(cfg["detection"]["face_score_threshold"])
    except DetectionError as exc:
        log.error("%s", exc)
        return 2

    db = FaceDatabase(
        os.path.join(HERE, "faces", "people.json"), cfg["detection"]["recognition_threshold"]
    )
    log.info(
        "known faces: %d %s", len(db),
        f"({db.sample_counts()})" if len(db) else "-- run enroll.py to add some",
    )

    # --- notification channels --------------------------------------------
    # One source of truth, shared with test_phone.py, so what the self-test
    # verifies is exactly what runs here.
    phone_channels = notifiers.build_phone_channels(cfg)
    console = ConsoleNotifier(
        enabled=bool(cfg["console"].get("enabled", True)),
        snapshot_dir=os.path.join(HERE, cfg["storage"]["snapshot_dir"]),
    )

    if not phone_channels and not console.enabled:
        log.error("No notification channel is enabled, so nothing could ever reach your phone.")
        for problem in notifiers.channel_setup_problems(cfg):
            log.error("  %s", problem)
        return 2
    if not phone_channels:
        log.warning("No phone channel configured -- alerts will only print to the console.")
        for problem in notifiers.channel_setup_problems(cfg):
            log.warning("  %s", problem)
        log.warning("  Quickest route: ntfy -- install the ntfy app, subscribe to a secret "
                    "topic, paste it into config.json under ntfy, then run test_phone.py.")

    if args.check:
        try:
            cap, label = FrameSource(cfg, source, args.loop).open()
            cap.release()
            log.info("source check OK (%s)", label)
        except (RuntimeError, FileNotFoundError, OSError) as exc:
            log.error("source check FAILED: %s", exc)
            return 2
        live = ", ".join(type(c).__name__.replace("Notifier", "") for c in phone_channels)
        log.info("phone channels: %s", live or "none (console only)")
        log.info("All good. Start it with:  python roomwatch.py")
        return 0

    # --- room state --------------------------------------------------------
    night = cfg["alerts"]["night_mode"]
    presence = RoomPresence(
        grace_seconds=cfg["detection"]["presence_grace_seconds"],
        track_max_age=cfg["detection"]["track_max_age_seconds"],
        iou_threshold=cfg["detection"]["track_iou_threshold"],
        min_hit_ratio=cfg["detection"]["min_hit_ratio"],
        unknown_cooldown=cfg["alerts"]["unknown_cooldown_seconds"],
        resend_while_present=cfg["alerts"]["resend_while_present_seconds"],
        heartbeat_minutes=cfg["alerts"]["heartbeat_minutes"],
        send_on_enter=cfg["alerts"]["send_on_person_enter"],
        send_on_leave=cfg["alerts"]["send_on_person_leave"],
        send_on_unknown=cfg["alerts"]["send_on_unknown"],
        night_mode=(int(night["start_hour"]), int(night["end_hour"])) if night.get("enabled") else None,
    )

    state = {
        "alerts_muted": bool(args.pause) or os.path.exists(PAUSE_FILE),
        "last_frame": None,
        "last_people": "",
        "started": time.time(),
        "frames": 0,
        "detect_calls": 0,
        "latency_ms": 0.0,
        "pushed": 0,
        "snap_seq": 0,
    }
    lock = threading.Lock()

    snap_dir = os.path.join(HERE, cfg["storage"]["snapshot_dir"])
    os.makedirs(snap_dir, exist_ok=True)

    # Alerts are handed to a worker thread: a slow phone/network must never
    # stall the capture loop, or you miss the next person walking in.
    outbox: queue.Queue = queue.Queue(maxsize=8)

    def dispatch(event, frame, tracks) -> None:
        photo = None
        if event.photo_wanted and frame is not None:
            try:
                annotated = render.annotate(
                    frame, tracks, event.headline, event.detail,
                    max_width=cfg["photo"]["max_width"],
                    status=f"RoomWatch  {render.stamp()}",
                )
                photo = render.encode_jpeg(annotated, cfg["photo"]["quality"])
                if cfg["photo"].get("save_snapshots", True):
                    # Two people arriving in the same second must not overwrite
                    # each other's snapshot, hence the per-process sequence.
                    with lock:
                        state["snap_seq"] += 1
                        seq = state["snap_seq"]
                    path = os.path.join(
                        snap_dir, f"{int(time.time())}_{seq:02d}_{event.kind}.jpg"
                    )
                    with open(path, "wb") as fh:
                        fh.write(photo)
                    prune_snapshots(snap_dir, cfg["storage"]["keep_last_n"])
            except Exception:  # noqa: BLE001
                log.exception("could not build the photo for a %s event", event.kind)
                photo = None

        silent = event.kind == "heartbeat"
        caption = f"<b>{event.headline}</b>\n{event.detail}"
        for ch in phone_channels:
            try:
                if photo is not None:
                    ch.send_photo(photo, caption, silent=silent)
                else:
                    ch.send_message(caption, silent=silent)
            except Exception:  # noqa: BLE001
                log.exception("channel %s failed", type(ch).__name__)
        if console.enabled:
            console.send(event.headline, event.detail, photo)
        with lock:
            state["pushed"] += 1

    def sender_worker() -> None:
        while True:
            item = outbox.get()
            if item is None:
                return
            try:
                dispatch(*item)
            except Exception:  # noqa: BLE001
                log.exception("alert dispatch failed")

    threads = [threading.Thread(target=sender_worker, daemon=True, name="sender")]
    for t in threads:
        t.start()

    # --- main loop ---------------------------------------------------------
    delay = float(cfg["runtime"]["startup_delay_seconds"])
    if delay > 0 and source is None:
        log.info("waiting %.0fs before claiming the camera (letting other apps release it)", delay)
        time.sleep(delay)

    try:
        src = FrameSource(cfg, source, args.loop)
    except FileNotFoundError as exc:
        log.error("%s", exc)
        return 2

    try:
        cap, label = src.open()
    except (RuntimeError, OSError) as exc:
        log.error("%s", exc)
        return 2

    log.info(
        "RoomWatch started | source=%s | %.1f fps | alerts %s | channels: %s",
        label,
        float(cfg["camera"]["target_fps"]),
        "MUTED" if state["alerts_muted"] else "on",
        ", ".join(type(c).__name__ for c in phone_channels) or "console only",
    )
    if state["alerts_muted"] and os.path.exists(PAUSE_FILE):
        log.info("muted because the %s file exists -- run resume.bat to start "
                 "sending alerts again", os.path.basename(PAUSE_FILE))

    frame_interval = 1.0 / max(0.5, float(cfg["camera"]["target_fps"]))
    every_n = max(1, int(cfg["detection"]["detect_every_n_frames"]))
    last_log = time.monotonic()  # so the first heartbeat waits 30s, not fires now
    last_reconnect = time.monotonic()
    stop = threading.Event()

    def _handle_signal(_signum, _frame):
        # Signal handlers may only touch the event; the loop does the cleanup.
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (ValueError, OSError, AttributeError):
            pass  # not the main thread, or unsupported on this platform

    try:
        while not stop.is_set():
            if stop.wait(frame_interval):
                break

            # A flaky UVC driver can raise here rather than returning False, and
            # losing the watcher to one bad frame defeats the whole point.
            try:
                ok, frame = cap.read()
            except cv2.error as exc:
                log.warning("camera read failed: %s", str(exc)[:200])
                ok, frame = False, None

            if not ok or frame is None:
                if src.exhausted():
                    log.info("replay finished after %d frames", state["frames"])
                    break
                if src.is_live:
                    if time.monotonic() - last_reconnect > float(cfg["camera"]["reconnect_seconds"]):
                        last_reconnect = time.monotonic()
                        log.warning("camera stopped delivering frames, reopening it")
                        cap.release()
                        try:
                            cap, label = src.open()
                        except (RuntimeError, OSError) as exc:
                            log.error("%s", exc)
                            stop.wait(2.0)
                            continue
                    continue
                continue

            state["frames"] += 1
            do_detect = every_n == 1 or (state["frames"] - 1) % every_n == 0

            if do_detect:
                t0 = time.perf_counter()
                try:
                    detections = detect_and_label(engine, db, frame)
                except DetectionError as exc:
                    log.error("%s", exc)
                    stop.wait(2.0)
                    continue
                state["detect_calls"] += 1
                state["latency_ms"] = 0.8 * state["latency_ms"] + 0.2 * (
                    (time.perf_counter() - t0) * 1000.0
                )
                events, tracks = presence.update(detections)
            else:
                # Skip inference, but never the state machine: feeding it empty
                # frames would age out live tracks and fake a "left" event.
                events, tracks = [], presence.live_tracks()

            with lock:
                state["last_frame"] = frame
                state["last_people"] = presence.describe()

            for event in events:
                # Muted by --pause at startup, or by the `paused` sentinel file
                # that pause.bat creates. Checked per event so toggling it takes
                # effect immediately, with no restart.
                if state["alerts_muted"] or os.path.exists(PAUSE_FILE):
                    if not state["alerts_muted"]:
                        state["alerts_muted"] = True
                        log.info("paused -- %s now exists, run resume.bat to start again",
                                 os.path.basename(PAUSE_FILE))
                    log.info("[muted]  %-9s %s", event.kind, event.headline)
                    continue
                if state["alerts_muted"]:
                    log.info("alerts resumed")
                state["alerts_muted"] = False
                log.info("[%-9s] %s", event.kind, event.headline)
                try:
                    outbox.put_nowait((event, frame.copy(), list(tracks)))
                except queue.Full:
                    log.warning("outbox full, dropping a %s alert", event.kind)

            if time.monotonic() - last_log > 30:
                last_log = time.monotonic()
                log.info(
                    "alive | %d frames | %d face(s) | room: %s | %.0f ms/frame",
                    state["frames"], len(tracks), presence.describe(), state["latency_ms"],
                )

    except KeyboardInterrupt:
        pass
    finally:
        log.info("stopping")
        try:
            cap.release()
        except Exception:  # noqa: BLE001
            pass

    # Flush anything queued before shutdown so the last person is still reported.
    outbox.put(None)
    for t in threads:
        if t.name == "sender":
            t.join(timeout=20)

    log.info("RoomWatch stopped after %d frames, %d alerts sent",
             state["frames"], state["pushed"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
