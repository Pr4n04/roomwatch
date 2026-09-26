"""Track identity across frames, decide who is in the room, emit discrete events.

The hard part of "tell me who walked in" is not detecting a face, it is not
sending 40 alerts a minute. This module owns that decision:

  * a per-frame face box is stitched into a persistent track (IoU + embedding)
  * a track's name is decided by a quality-weighted vote, not a single frame,
    so a half-lit frame cannot rename someone
  * events fire on *transitions* -- arrival and departure -- plus rate-limited
    unknown-face alerts and an optional periodic heartbeat
"""

from __future__ import annotations

import itertools
import time
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from facedb import UNKNOWN, l2_normalise

MERGE_EMBEDDING_SIM = 0.55
LIVE_NAME_LOCK = 0.42  # once a track has a name, keep it until this drops


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    track_id: int
    box: tuple[int, int, int, int]
    centroid: tuple[float, float]
    embedding: np.ndarray | None
    first_seen: float
    last_seen: float
    hits: int = 1
    misses: int = 0
    best_score: float = 0.0
    votes: Counter = field(default_factory=Counter)
    name: str = UNKNOWN
    name_locked: bool = False

    @property
    def age(self) -> float:
        return self.last_seen - self.first_seen

    def update(self, box, centroid, embedding, name, score, quality, now) -> None:
        # Exponential moving centroid keeps the box stable for drawing while
        # still following someone who is moving.
        self.centroid = (
            0.7 * self.centroid[0] + 0.3 * centroid[0],
            0.7 * self.centroid[1] + 0.3 * centroid[1],
        )
        self.box = box
        self.last_seen = now
        self.hits += 1
        self.misses = 0
        self.best_score = max(self.best_score, score)

        if embedding is not None:
            if self.embedding is None:
                self.embedding = l2_normalise(embedding)
            else:
                blended = 0.85 * self.embedding + 0.15 * l2_normalise(embedding)
                self.embedding = l2_normalise(blended)

        # A good frame is worth more than a thumbnail-sized, blurry one.
        weight = 0.25 + quality
        self.votes[name] += weight
        self._recount()

    def _recount(self) -> None:
        if not self.votes:
            return
        top_name, top_weight = self.votes.most_common(1)[0]
        decided = top_name != UNKNOWN
        if self.name_locked and decided:
            # Only abandon a locked name if a rival clearly outvotes it.
            runner_up = next((w for n, w in self.votes.most_common(2) if n != top_name), 0.0)
            if runner_up > 0.6 * top_weight:
                self.name_locked = False
        if not self.name_locked:
            self.name = top_name
            if decided and top_weight >= 1.0:
                self.name_locked = True

    @property
    def hit_ratio(self) -> float:
        return self.hits / (self.hits + self.misses) if (self.hits + self.misses) else 0.0


@dataclass
class Event:
    kind: str  # "enter" | "leave" | "unknown" | "heartbeat"
    people: list[str]
    at: float
    photo_wanted: bool
    headline: str
    detail: str = ""


class RoomPresence:
    """Turns a stream of labelled faces into 'who is in the room' + events."""

    def __init__(
        self,
        grace_seconds: float = 30,
        track_max_age: float = 12,
        iou_threshold: float = 0.3,
        min_hit_ratio: float = 0.25,
        unknown_cooldown: float = 300,
        resend_while_present: float = 0,
        heartbeat_minutes: float = 0,
        send_on_enter: bool = True,
        send_on_leave: bool = True,
        send_on_unknown: bool = True,
        night_mode: tuple[int, int] | None = None,
    ) -> None:
        self.grace = grace_seconds
        self.track_max_age = track_max_age
        self.iou_threshold = iou_threshold
        self.min_hit_ratio = min_hit_ratio
        self.unknown_cooldown = unknown_cooldown
        self.resend_while_present = resend_while_present
        self.heartbeat_seconds = heartbeat_minutes * 60 if heartbeat_minutes else 0
        self.send_on_enter = send_on_enter
        self.send_on_leave = send_on_leave
        self.send_on_unknown = send_on_unknown
        self.night_mode = night_mode

        self._ids = itertools.count(1)
        self.tracks: list[Track] = []
        self.present: dict[str, float] = {}  # name -> last seen
        self._last_unknown_at = 0.0
        self._last_enter_at: dict[str, float] = {}
        self._last_heartbeat = 0.0
        self._ever_seen: set[str] = set()

    # ------------------------------------------------------------- night mode

    def _in_night_mode(self, now_ts: float) -> bool:
        """start_hour=23, end_hour=7 means 'quiet from 23:00 through 06:59'."""
        if not self.night_mode:
            return False
        start, end = self.night_mode
        hour = time.localtime(now_ts).tm_hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end  # window wraps past midnight

    # ------------------------------------------------------------------ update

    def update(
        self,
        detections: list[dict],
        now: float | None = None,
        wall_clock: float | None = None,
    ) -> tuple[list[Event], list[Track]]:
        """detections: [{"box", "centroid", "embedding", "name", "score", "quality"}]"""
        now = time.monotonic() if now is None else now
        wall_clock = time.time() if wall_clock is None else wall_clock

        self._associate(detections, now)
        self._expire(now)
        return self._events(now, wall_clock), self.live_tracks()

    def _associate(self, detections: list[dict], now: float) -> None:
        for det in detections:
            best, best_score = None, 0.0
            for track in self.tracks:
                overlap = iou(track.box, det["box"])
                if overlap < self.iou_threshold:
                    continue
                sim = 1.0
                if track.embedding is not None and det.get("embedding") is not None:
                    sim = float(track.embedding @ l2_normalise(det["embedding"]))
                    # Same place AND same face -> very likely the same person.
                    if sim < MERGE_EMBEDDING_SIM and overlap < 0.55:
                        continue
                score = overlap * 0.5 + sim * 0.5
                if score > best_score:
                    best, best_score = track, score

            if best is not None:
                best.update(
                    det["box"],
                    det["centroid"],
                    det.get("embedding"),
                    det["name"],
                    det["score"],
                    det["quality"],
                    now,
                )
            else:
                track = Track(
                    track_id=next(self._ids),
                    box=det["box"],
                    centroid=det["centroid"],
                    embedding=None if det.get("embedding") is None else l2_normalise(det["embedding"]),
                    first_seen=now,
                    last_seen=now,
                    best_score=det["score"],
                )
                track.votes[det["name"]] += 0.25 + det["quality"]
                track._recount()
                self.tracks.append(track)

    def _expire(self, now: float) -> None:
        keep: list[Track] = []
        for track in self.tracks:
            if now - track.last_seen <= self.track_max_age:
                track.misses += 1
                keep.append(track)
        self.tracks = keep

    # ------------------------------------------------------------------ state

    def live_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.hit_ratio >= self.min_hit_ratio]

    def occupancy(self) -> list[str]:
        """Names believed to be in the room right now, unknown people last."""
        known = [n for n in self.present if n != UNKNOWN]
        return sorted(known) + ([UNKNOWN] if UNKNOWN in self.present else [])

    def is_empty(self) -> bool:
        return not self.present

    def describe(self) -> str:
        people = self.occupancy()
        if not people:
            return "Nobody"
        return " + ".join(people)

    def _sync_present(self, now: float) -> tuple[list[str], list[str]]:
        """Returns (arrived, departed) relative to the previous known state."""
        fresh: dict[str, float] = {}
        for track in self.live_tracks():
            name = track.name
            if name == UNKNOWN and track.age < 0.8:
                continue  # don't announce a face in its first frame
            fresh[name] = max(fresh.get(name, 0.0), now)

        # Grace keeps someone "in the room" through a blink, a turn away, or a
        # moment of motion blur -- otherwise the door becomes a slot machine.
        for name, seen_at in list(self.present.items()):
            if name not in fresh and now - seen_at <= self.grace:
                fresh[name] = seen_at

        previous = set(self.present)
        current = set(fresh)
        self.present = fresh
        self._ever_seen |= current
        return sorted(current - previous), sorted(previous - current)

    # ----------------------------------------------------------------- events

    def _events(self, now: float, wall_clock: float) -> list[Event]:
        events: list[Event] = []
        arrived, departed = self._sync_present(now)
        stamp = time.strftime("%H:%M", time.localtime(wall_clock))
        night = self._in_night_mode(wall_clock)

        for name in arrived:
            who = "Someone" if name == UNKNOWN else name
            if name == UNKNOWN:
                if not self.send_on_unknown or now - self._last_unknown_at < self.unknown_cooldown:
                    continue
                self._last_unknown_at = now
                events.append(
                    Event(
                        kind="unknown",
                        people=[UNKNOWN],
                        at=now,
                        photo_wanted=True,
                        headline=f"{who} is in your room",
                        detail=(
                            f"{stamp} &mdash; face not in your enrolled list.\n"
                            f"Also here: {self._others_than(UNKNOWN) or 'nobody else'}."
                        ),
                    )
                )
                continue

            if night:
                # Quiet at night for people you already know.
                continue
            if not self.send_on_enter:
                continue
            if self.resend_while_present and now - self._last_enter_at.get(name, 0) < self.resend_while_present:
                continue
            self._last_enter_at[name] = now
            events.append(
                Event(
                    kind="enter",
                    people=[name],
                    at=now,
                    photo_wanted=True,
                    headline=f"{name} is in your room",
                    detail=f"{stamp} &mdash; currently: {self.describe()}.",
                )
            )

        for name in departed:
            if name == UNKNOWN:
                continue
            if night or not self.send_on_leave:
                continue
            events.append(
                Event(
                    kind="leave",
                    people=[name],
                    at=now,
                    photo_wanted=False,
                    headline=f"{name} left your room",
                    detail=f"{stamp} &mdash; now: {self.describe()}.",
                )
            )

        if self.heartbeat_seconds and (self.present or not self._ever_seen):
            if now - self._last_heartbeat >= self.heartbeat_seconds:
                self._last_heartbeat = now
                events.append(
                    Event(
                        kind="heartbeat",
                        people=self.occupancy(),
                        at=now,
                        photo_wanted=bool(self.present),
                        headline="Room check-in",
                        detail=(
                            f"{time.strftime('%H:%M', time.localtime(wall_clock))} &mdash; "
                            f"in the room: {self.describe()}."
                        ),
                    )
                )

        return events

    def _others_than(self, name: str) -> str:
        others = [n for n in self.occupancy() if n != name]
        return " + ".join(others) if others else ""
