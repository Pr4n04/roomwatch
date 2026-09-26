"""Face detection (YuNet) + face recognition (SFace) on plain CPU.

Both models ship inside the opencv-python wheel, so this is the whole
dependency story: `pip install opencv-python numpy`. No torch, no dlib, no
CUDA, nothing that turns into a two-hour build failure on a fresh Windows box.

Reference frame for both APIs is the official OpenCV Zoo sample:
  https://github.com/opencv/opencv_zoo/blob/main/models/face_recognition_sface/demo.py
"""

from __future__ import annotations

import quietcv  # noqa: F401  -- must precede `import cv2`
import cv2
import numpy as np

from fetch_models import ensure_models

# SFace was trained on faces aligned so these two eye points land exactly here
# in a 112x112 crop.
_DST_EYE_R = np.array([38.2946, 51.6963], dtype=np.float32)
_DST_EYE_L = np.array([73.5318, 51.5014], dtype=np.float32)

# YuNet output row: x, y, w, h, then 5 landmark pairs, then score.
_SCORE_IDX = 14
_RIGHT_EYE = slice(4, 6)
_LEFT_EYE = slice(6, 8)

CROP_SIZE = 112


def eye_alignment_matrix(src: np.ndarray, dst: np.ndarray) -> np.ndarray | None:
    """2x3 similarity transform taking the detected eye pair onto the SFace template.

    Solved by hand rather than with cv2.getAffineTransform: OpenCV 5 removed the
    two-point overload, so the library call would work on some Windows machines
    and fail on others. Two correspondences are exactly enough to pin down
    scale, rotation and translation, so this is a closed form, not an
    approximation:

        d_src = src[1] - src[0]      d_dst = dst[1] - dst[0]
        s     = |d_dst| / |d_src|    t     = atan2(d_dst) - atan2(d_src)
    """
    if src.shape != (2, 2) or dst.shape != (2, 2):
        return None
    if not (np.isfinite(src).all() and np.isfinite(dst).all()):
        return None

    d_src = (src[1] - src[0]).astype(np.float64)
    d_dst = (dst[1] - dst[0]).astype(np.float64)
    norm_src = float(np.hypot(*d_src))
    if norm_src < 5.0:
        # Landmarks collapsed: the face is too small or too blurred to trust.
        return None

    scale = float(np.hypot(*d_dst)) / norm_src
    theta = float(np.arctan2(d_dst[1], d_dst[0]) - np.arctan2(d_src[1], d_src[0]))

    cos_t, sin_t = scale * np.cos(theta), scale * np.sin(theta)
    # Rotate/scale src[0] onto dst[0]; that translation makes src[1] land on dst[1].
    tx = float(dst[0][0]) - (cos_t * src[0][0] - sin_t * src[0][1])
    ty = float(dst[0][1]) - (sin_t * src[0][0] + cos_t * src[0][1])
    return np.array([[cos_t, -sin_t, tx], [sin_t, cos_t, ty]], dtype=np.float32)


class DetectionError(RuntimeError):
    pass


class FaceEngine:
    """Detect faces in a BGR frame and label them against a FaceDatabase."""

    def __init__(self, score_threshold: float = 0.75, models_path: str | None = None) -> None:
        paths = ensure_models(models_path)
        self.score_threshold = float(score_threshold)

        try:
            self.detector = cv2.FaceDetectorYN.create(
                paths["face_detection_yunet_2023mar.onnx"], "", (320, 320), self.score_threshold, 0.3, 5000
            )
        except cv2.error as exc:
            raise DetectionError(
                "Could not load YuNet face detector. The model file is likely a GitHub "
                f"error page instead of the ONNX model.\n  {exc}"
            ) from exc

        try:
            self.recognizer = cv2.FaceRecognizerSF.create(
                paths["face_recognition_sface_2021dec.onnx"], ""
            )
        except cv2.error as exc:
            raise DetectionError(f"Could not load SFace recogniser. {exc}") from exc

    # ------------------------------------------------------------------ detect

    def detect_faces(self, frame: np.ndarray) -> list[np.ndarray]:
        """Return YuNet rows for faces above the score threshold (may be empty)."""
        if frame is None or getattr(frame, "size", 0) == 0:
            return []
        h, w = frame.shape[:2]
        if h < 2 or w < 2:
            return []
        self.detector.setInputSize((w, h))
        try:
            _, faces = self.detector.detect(frame)
        except cv2.error as exc:
            raise DetectionError(f"YuNet inference failed: {exc}") from exc
        if faces is None or len(faces) == 0:
            return []
        return [f for f in faces if float(f[_SCORE_IDX]) >= self.score_threshold]

    def embed(self, frame: np.ndarray, face: np.ndarray) -> np.ndarray | None:
        """112x112-aligned 128-d embedding, or None if the face is unusable."""
        try:
            src = np.array(
                [
                    np.asarray(face[_RIGHT_EYE], dtype=np.float32),
                    np.asarray(face[_LEFT_EYE], dtype=np.float32),
                ],
                dtype=np.float32,
            )
            matrix = eye_alignment_matrix(src, np.array([_DST_EYE_R, _DST_EYE_L], dtype=np.float32))
            if matrix is None:
                return None

            # Warping by the similarity transform also normalises head roll and
            # distance, which is most of why SFace matches reliably in practice.
            crop = cv2.warpAffine(
                frame,
                matrix,
                (CROP_SIZE, CROP_SIZE),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0),
            )
            feature = self.recognizer.feature(crop)
            if feature is None or feature.size != 128:
                return None
            return feature.reshape(128).astype(np.float32)
        except (cv2.error, ValueError):
            return None

    # ------------------------------------------------------------------ colour

    @staticmethod
    def box(face: np.ndarray) -> tuple[int, int, int, int]:
        x, y, w, h = (float(face[0]), float(face[1]), float(face[2]), float(face[3]))
        return int(x), int(y), int(w), int(h)

    @staticmethod
    def quality(face: np.ndarray) -> float:
        """Cheap 'is this face big and sharp enough to trust' score in 0..1."""
        _, _, w, h = FaceEngine.box(face)
        area = float(w) * float(h)
        # ~16% of a 640x480 frame is a passport-photo-size face.
        size_score = min(1.0, area / (640 * 480 * 0.16))
        eye_gap = float(np.linalg.norm(np.asarray(face[_RIGHT_EYE]) - np.asarray(face[_LEFT_EYE])))
        gap_score = min(1.0, eye_gap / 28.0)
        return 0.65 * size_score + 0.35 * gap_score
