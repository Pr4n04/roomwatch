"""Teach RoomWatch what your housemates look like.

    python enroll.py Alice                 # 20 samples from the live camera
    python enroll.py Alice --from photos/alice/
    python enroll.py --list
    python enroll.py --remove Alice

Accuracy is decided almost entirely here, not by any setting in config.json.
SFace is a 128-d embedding model; it matches reliably when a person has several
genuinely different photos enrolled, and unreliably from one. Aim for 15-25
samples per person covering:

  * head roughly centred, roughly filling the middle third of the frame
  * looking straight at / 30 degrees off / 45 degrees off
  * a couple of metres back and one close up
  * with and without glasses, different hair, a hat on one
  * bright window behind you AND a dim room (the door-end is usually dim)

Do not train on a photo of a person printed on a screen, and keep the room lit.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time

import quietcv  # noqa: F401  -- must precede `import cv2`
import cv2
import numpy as np

from config import base_dir
from faceengine import DetectionError, FaceEngine
from facedb import FaceDatabase, infer_name_from_path

HERE = base_dir()
DB_PATH = os.path.join(HERE, "faces", "people.json")

WINDOW = "RoomWatch - enrolling"
GUIDE = [
    "Look at the camera, keep your face in the middle of the frame.",
    "Space = capture one sample.  r = reset this session.  q = finish.",
]


def _draw(img, faces, count, total, hint) -> np.ndarray:
    out = img.copy()
    for f in faces:
        x, y, w, h = FaceEngine.box(f)
        cv2.rectangle(out, (x, y), (x + w, y + h), (90, 220, 90), 2)
    band = out.shape[0] - 96
    out[band:, :] = (26, 26, 30)
    cv2.putText(out, f"captured {count}/{total}", (18, band + 32),
                cv2.FONT_HERSHEY_SIMPLEX, 0.75, (245, 245, 245), 2, cv2.LINE_AA)
    cv2.putText(out, hint[:78], (18, band + 64),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 220, 255), 1, cv2.LINE_AA)
    if len(faces) != 1:
        cv2.putText(out, f"{len(faces)} faces detected - want exactly 1", (18, band + 88),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (70, 70, 245), 1, cv2.LINE_AA)
    return out


def enroll_from_camera(engine: FaceEngine, db: FaceDatabase, name: str, total: int,
                        camera_index: int = 0) -> int:
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
    if not cap.isOpened():
        cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        print(f"ERROR: cannot open camera {camera_index}. Close Windows Camera / Teams / Zoom "
              f"and retry, or try another index with --camera 1.")
        return 1

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 960)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    added = 0
    hint = GUIDE[0]
    last_capture = 0.0
    print("\n".join(GUIDE))

    try:
        while added < total:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("ERROR: camera stopped delivering frames.")
                return 1

            faces = engine.detect_faces(frame)
            window = _draw(frame, faces, added, total, hint)
            cv2.imshow(WINDOW, window)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord("r"):
                added = 0
                print("session reset")
                continue

            if key == 32 and len(faces) == 1 and time.time() - last_capture > 0.25:
                embedding = engine.embed(frame, faces[0])
                if embedding is None:
                    hint = "Face too small or blurry - move closer."
                    continue
                db.add(name, embedding)
                added += 1
                last_capture = time.time()
                hint = f"Saved {added}/{total}. Vary your angle/light, or press q to finish."
                print(f"  sample {added}/{total}")
            elif key == 32 and len(faces) != 1:
                hint = f"{len(faces)} faces detected - only the person enrolling should be in frame."
    finally:
        cap.release()
        cv2.destroyAllWindows()

    if added:
        db.save()
        print(f"\nAdded {added} sample(s) for {name}. {name} now has {db.sample_counts()[name]}.")
    else:
        print("\nNo samples captured. Nothing changed.")
    return 0 if added else 2


def enroll_from_folder(engine: FaceEngine, db: FaceDatabase, folder: str, explicit_name: str | None) -> int:
    files: list[str] = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.bmp", "*.webp"):
        files.extend(glob.glob(os.path.join(folder, ext)))
    files = sorted(files)
    if not files:
        print(f"ERROR: no images found in {folder}")
        return 1

    total_added = 0
    per_person: dict[str, int] = {}
    skipped: list[str] = []

    for path in files:
        name = explicit_name or infer_name_from_path(path)
        img = cv2.imread(path)
        if img is None:
            skipped.append(f"{os.path.basename(path)} (unreadable)")
            continue
        faces = engine.detect_faces(img)
        if len(faces) != 1:
            # Crop to the largest face when a group shot, skip when ambiguous.
            if len(faces) > 1:
                faces = [max(faces, key=lambda f: float(f[2]) * float(f[3]))]
            else:
                skipped.append(f"{os.path.basename(path)} (no face)")
                continue
        embedding = engine.embed(img, faces[0])
        if embedding is None:
            skipped.append(f"{os.path.basename(path)} (unusable crop)")
            continue
        db.add(name, embedding)
        per_person[name] = per_person.get(name, 0) + 1
        total_added += 1

    if total_added:
        db.save()

    print(f"\nEnrolled {total_added} sample(s) from {len(files)} image(s):")
    for name, count in sorted(per_person.items()):
        print(f"  {name:<20} {count:>3} added   (now {db.sample_counts()[name]} total)")
    if skipped:
        print(f"\nSkipped {len(skipped)}:")
        for line in skipped[:15]:
            print(f"  {line}")
        if len(skipped) > 15:
            print(f"  ... and {len(skipped) - 15} more")
    return 0 if total_added else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrol faces for RoomWatch")
    parser.add_argument("name", nargs="?", help="person's name, e.g. Alice")
    parser.add_argument("--from", dest="folder", help="folder of photos to enroll from")
    parser.add_argument("--camera", type=int, help="camera index (default 0)")
    parser.add_argument("--samples", type=int, default=20, help="samples to capture (default 20)")
    parser.add_argument("--list", action="store_true", help="list enrolled people and exit")
    parser.add_argument("--remove", help="remove a person and exit")
    args = parser.parse_args()

    db = FaceDatabase(DB_PATH)

    if args.list:
        if not len(db):
            print("Nobody enrolled yet.")
            print("  python enroll.py Alice                    (live camera)")
            print("  python enroll.py --from C:/pics/alice/    (existing photos)")
            return 0
        print(f"{'name':<24}{'samples':>9}   quality")
        for name, count in db.sample_counts().items():
            verdict = "good" if count >= 12 else "thin - add more photos" if count < 6 else "ok"
            print(f"{name:<24}{count:>9}   {verdict}")
        print(f"\n{len(db)} person(s), {sum(db.sample_counts().values())} samples total.")
        return 0

    if args.remove:
        n = db.remove(args.remove)
        print(f"Removed {args.remove} ({n} samples)." if n else f"{args.remove} was not enrolled.")
        return 0 if n else 2

    if not args.name and not args.folder:
        parser.print_help()
        return 2

    try:
        engine = FaceEngine(0.6)  # enrolment is forgiving: capture more, prune later
    except DetectionError as exc:
        print(f"ERROR: {exc}")
        return 2

    if args.folder:
        if not args.name:
            print("Enrolling from a folder: names come from the filenames "
                  "(e.g. Alice_01.jpg -> Alice). Pass a name to force one instead.")
        return enroll_from_folder(engine, db, args.folder, args.name)

    return enroll_from_camera(engine, db, args.name.strip(), max(1, args.samples),
                              camera_index=args.camera or 0)


if __name__ == "__main__":
    sys.exit(main())
