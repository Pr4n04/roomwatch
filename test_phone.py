"""Send one test alert to your phone. No camera, no enrolled faces needed.

Run this right after filling in config.json. If your phone buzzes and shows a
photo, the phone link works and you can move on to enrolling faces.

    venv\\Scripts\\python.exe test_phone.py
"""

from __future__ import annotations

import quietcv  # noqa: F401  (must precede cv2; silences OpenCV's stderr noise)

import argparse
import os
import sys
import uuid

import cv2
import numpy as np

import config as config_mod
import notifiers
from render import annotate, encode_jpeg

HERE = os.path.dirname(os.path.abspath(__file__))


def sample_frame(text: list[str]) -> "np.ndarray":
    """A still 'camera' image, so the test photo looks like a real alert."""
    w, h = 960, 720
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    blue = np.full((h, w), 40, np.float32) + 60 * y
    green = np.full((h, w), 44, np.float32) + 70 * y
    red = np.full((h, w), 52, np.float32) + 80 * x
    frame = np.clip(np.stack([blue, green, red], axis=-1), 0, 255).astype(np.uint8)
    for i, line in enumerate(text):
        y0 = 150 + i * 52
        cv2.putText(frame, line, (60, y0), cv2.FONT_HERSHEY_SIMPLEX, 1.15, (255, 255, 255), 3, cv2.LINE_AA)
        cv2.putText(frame, line, (60, y0), cv2.FONT_HERSHEY_SIMPLEX, 1.15, (30, 30, 30), 1, cv2.LINE_AA)
    return frame


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Send a test photo alert to your phone.")
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"),
                    help="path to config.json (defaults to the one next to this script)")
    ap.add_argument("--text", default="Test alert: the laptop can see you and send a photo.")
    ap.add_argument("--silent", action="store_true", help="Deliver without a sound/vibration.")
    args = ap.parse_args(argv)

    # config.json is gitignored because it holds a real ntfy topic, so a fresh
    # clone has only the tracked template. Say that plainly instead of blaming
    # a channel that was never switched off.
    if not os.path.exists(args.config):
        example = os.path.join(HERE, "config.example.json")
        print(f"No settings file at {args.config}\n")
        print("It is not in the repo on purpose: it holds your ntfy topic, which is")
        print("a secret on a public server. setup.bat creates it from the template.\n")
        if os.path.exists(example):
            print("Either run  setup.bat  and let it finish, or copy it yourself:\n")
            print(f'    copy "{example}" "{args.config}"\n')
        print(f"Then set your ntfy topic in {os.path.basename(args.config)} and re-run.")
        return 2

    cfg = config_mod.load(args.config)
    channels = notifiers.build_phone_channels(cfg)

    if not channels:
        print("No phone channel is set up yet, so there is nowhere to send this.\n")
        for problem in notifiers.channel_setup_problems(cfg):
            print(f"  * {problem}")

        # ntfy's "Secret"/"Secured" button only exists to invent a long random
        # name, and it is not in every version of the app. Roll one here instead
        # so there is nothing to go hunting for in the UI.
        suggested = "room-" + uuid.uuid4().hex[:20]
        print("\nThe quickest route is ntfy (no account, no token):")
        print("  1. Install the ntfy app on your phone (Play Store or App Store).")
        print("  2. Open it, tap the + button, and subscribe to a topic.")
        print(f"     Use this name, generated for you:\n\n         {suggested}\n")
        print('     ("Secret"/"Secured" in some versions of the app just invents a')
        print("     random name for you. This is the same thing, and you can ignore")
        print("     that button entirely. Anything long and random is fine.)")
        print("  3. In config.json, set ntfy > \"topic\" to that name and")
        print('     "enabled" to true, then run this script again.')
        print("\nWhy the name has to be random: on the public ntfy.sh server, anyone")
        print("who knows a topic name can read that topic, so 'roomwatch' on its")
        print("own would let anyone who guessed it see your room photos.")
        print("\nA random name is obscurity, though, not a password. If you want a")
        print("real login in front of your alerts, self-host ntfy and set ntfy >")
        print('"token" (or "username" and "password") as well -- see "Private')
        print('instead: self-host ntfy" in the README.')
        return 2

    print(f"Configured channels: {', '.join(type(c).__name__.replace('Notifier', '') for c in channels)}\n")

    frame = sample_frame([
        "RoomWatch test",
        os.path.basename(os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "your laptop"),
        "If you can read this on your phone, it works.",
    ])
    jpeg = encode_jpeg(
        annotate(frame, [], "RoomWatch test alert", args.text),
        quality=int((cfg.get("photo") or {}).get("quality") or 82),
    )

    ok = 0
    for channel in channels:
        name = type(channel).__name__.replace("Notifier", "")
        sent = channel.send_photo(jpeg, args.text, silent=args.silent, filename="roomwatch_test.jpg")
        print(f"  {'sent' if sent else 'FAILED'}  ->  {name}")
        ok += bool(sent)
        # Also exercise the text path; leave alerts are text-only and you want
        # to know those work before you rely on them.
        sent_text = channel.send_message("RoomWatch test: text alerts work too.", silent=args.silent)
        if sent_text:
            print(f"  sent  ->  {name} (text)")
            ok += 1
        else:
            print(f"  FAILED  ->  {name} (text)")

    if ok:
        print(f"\n{ok} message(s) sent. Check your phone now (it may take a few seconds).")
        print("Next: enrol faces with  venv\\Scripts\\python.exe enroll.py \"Alice\"")
        return 0

    print("\nNothing got through. Check roomwatch.log for the reason, then re-run this.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
