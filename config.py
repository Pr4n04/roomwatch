"""config.json loading with defaults, so a missing or partial config still runs."""

from __future__ import annotations

import copy
import json
import os

DEFAULTS: dict = {    "camera": {
        "camera_index": 0,
        "use_dshow": True,
        "frame_width": 960,
        "frame_height": 720,
        "target_fps": 5,
        "reconnect_seconds": 5,
    },
    "detection": {
        "detect_every_n_frames": 1,
        "face_score_threshold": 0.75,
        "recognition_threshold": 0.363,
        "track_iou_threshold": 0.3,
        "track_max_age_seconds": 8,
        "presence_grace_seconds": 20,
        "min_hit_ratio": 0.25,
    },
    "alerts": {
        "send_on_person_enter": True,
        "send_on_person_leave": True,
        "send_on_unknown": True,
        "unknown_cooldown_seconds": 300,
        "resend_while_present_seconds": 0,
        "heartbeat_minutes": 0,
        "night_mode": {"enabled": False, "start_hour": 23, "end_hour": 7},
    },
    "photo": {"quality": 82, "max_width": 1280, "draw_boxes": True, "save_snapshots": True},
    "ntfy": {
        "enabled": False,
        "server": "https://ntfy.sh",
        "topic": "",
        # Credentials are only needed for a self-hosted server started with
        # auth-default-access: deny-all. Empty means "open", which is correct
        # for ntfy.sh and the only thing that server supports.
        "token": "",
        "username": "",
        "password": "",
    },
    "webhook": {
        "enabled": False,
        "url": "",
        "field_name": "file",
        "header_name": "X-Webhook-Secret",
        "header_value": "",
    },
    "console": {"enabled": True},
    "storage": {"snapshot_dir": "snapshots", "keep_last_n": 200},
    "runtime": {
        "startup_delay_seconds": 4,
        "log_level": "INFO",
        "log_file": "roomwatch.log",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if key.startswith("_"):
            continue  # JSON comments we keep in the file
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: str = "config.json") -> dict:
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULTS)
    with open(path, "r", encoding="utf-8") as fh:
        user = json.load(fh)
    return _deep_merge(DEFAULTS, user)


def base_dir() -> str:
    r"""Everything relative to config.json lives next to the script, not the cwd.

    Windows Task Scheduler starts programs in C:\Windows\System32, so a relative
    'snapshots' would otherwise land somewhere useless.
    """
    return os.path.dirname(os.path.abspath(__file__))
