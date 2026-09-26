"""Download the two OpenCV Zoo ONNX models RoomWatch needs.

Both run on plain CPU through cv2's dnn module -- no torch, no dlib, no CUDA.
Run automatically by roomwatch.py on first start, or manually:

    python fetch_models.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request

BASE = "https://github.com/opencv/opencv_zoo/raw/main/models"

MODELS = {
    "face_detection_yunet_2023mar.onnx": f"{BASE}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": f"{BASE}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}

MIN_BYTES = 50_000  # a truncated/failed download is smaller than this


def models_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")


def ensure_models(target_dir: str | None = None, quiet: bool = False) -> dict[str, str]:
    """Return {filename: path}, downloading anything missing or corrupt."""
    target_dir = target_dir or models_dir()
    os.makedirs(target_dir, exist_ok=True)

    paths: dict[str, str] = {}
    for filename, url in MODELS.items():
        path = os.path.join(target_dir, filename)
        paths[filename] = path

        if os.path.exists(path) and os.path.getsize(path) >= MIN_BYTES:
            continue

        if os.path.exists(path):
            if not quiet:
                print(f"[models] {filename} looks corrupt, re-downloading")
            os.remove(path)

        if not quiet:
            print(f"[models] downloading {filename} ...")
        tmp = path + ".part"
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as fh:
                total = 0
                while chunk := resp.read(65536):
                    fh.write(chunk)
                    total += len(chunk)
            if total < MIN_BYTES:
                raise IOError(f"only {total} bytes received")
            os.replace(tmp, path)
        except Exception as exc:  # noqa: BLE001
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError(
                f"Failed to download {filename}.\n"
                f"  url: {url}\n"
                f"  error: {exc}\n"
                f"  Check your internet connection, or download it manually in a\n"
                f"  browser and drop it into: {target_dir}"
            ) from exc

        if not quiet:
            digest = hashlib.sha256(open(path, "rb").read()).hexdigest()[:12]
            print(f"[models] ok  {filename}  {os.path.getsize(path) / 1024:.0f} KB  sha256:{digest}")

    return paths


if __name__ == "__main__":
    try:
        ensure_models(quiet=False)
    except RuntimeError as err:
        print(f"[models] ERROR {err}", file=sys.stderr)
        sys.exit(1)
    print(f"[models] all models ready in {models_dir()}")
