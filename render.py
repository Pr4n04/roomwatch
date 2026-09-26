"""Draw the annotated room photo that lands on the phone, and encode it to JPEG."""

from __future__ import annotations

import time

import cv2
import numpy as np

from facedb import UNKNOWN

# BGR
_GREEN = (90, 220, 90)
_RED = (70, 70, 245)
_AMBER = (0, 190, 245)
_GREY = (170, 170, 170)
_DARK = (28, 28, 32)

FONT = cv2.FONT_HERSHEY_SIMPLEX


def _label(img: np.ndarray, text: str, org: tuple[int, int], colour, scale: float = 0.6) -> None:
    (tw, th), baseline = cv2.getTextSize(text, FONT, scale, 2)
    x, y = org
    top = max(0, y - th - 6)
    cv2.rectangle(img, (x, top), (min(img.shape[1] - 1, x + tw + 10), min(img.shape[0] - 1, y + baseline + 2)), _DARK, -1)
    cv2.putText(img, text, (x + 5, y), FONT, scale, colour, 2, cv2.LINE_AA)


def annotate(
    frame: np.ndarray,
    tracks: list,
    headline: str,
    detail: str,
    max_width: int = 1280,
    status: str = "",
) -> np.ndarray:
    """Return a copy of frame with name boxes and a caption bar burned in."""
    out = frame.copy()
    for track in tracks:
        x, y, w, h = track.box
        known = track.name != UNKNOWN
        colour = _GREEN if known else _RED
        cv2.rectangle(out, (x, y), (x + w, y + h), colour, 2)

        pct = int(track.best_score * 100)
        _label(out, f"{track.name}  {pct}%", (x, y - 6), colour, 0.55)

    # Caption bar across the bottom.
    lines = [headline] + [ln for ln in detail.splitlines() if ln.strip()]
    if status:
        lines.append(status)
    line_h = 30
    bar_h = line_h * len(lines) + 18
    strip = np.zeros((bar_h, out.shape[1], 3), dtype=np.uint8)
    strip[:] = _DARK
    out = np.vstack([out, strip])
    for i, line in enumerate(lines):
        y = bar_h - (len(lines) - i - 1) * line_h - 10
        cv2.putText(out, line[:90], (16, y), FONT, 0.6, (245, 245, 245), 1, cv2.LINE_AA)

    if out.shape[1] > max_width:
        scale = max_width / out.shape[1]
        out = cv2.resize(
            out, (max_width, int(out.shape[0] * scale)), interpolation=cv2.INTER_AREA
        )
    return out


def encode_jpeg(frame: np.ndarray, quality: int = 82) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:
        raise RuntimeError("cv2.imencode failed to encode the JPEG")
    return buf.tobytes()


def stamp() -> str:
    return time.strftime("%a %d %b %H:%M:%S")
