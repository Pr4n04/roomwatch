"""Keeps OpenCV's own logging quiet.

Must be imported *before* `import cv2`, because the level is read once when the
extension module initialises its logger. OpenCV 5.x otherwise prints things like

    [ WARN:0] global net_impl_backend.cpp:345 setPreferableTarget Targets are not
    supported by the new graph engine for now

on every model load, which looks alarming but is entirely harmless -- RoomWatch
never asks for a specific compute target, so there is nothing to warn about.
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "0")
