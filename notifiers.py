"""Notification fan-out: ntfy, webhook, console.

Deliberately built on urllib from the standard library. The only third-party
dependency in the whole project is opencv + numpy, which keeps 'copy the folder
onto a fresh Windows laptop' a one-command install.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("roomwatch.notify")

_PLACEHOLDERS = ("PASTE_", "PASTE_YOUR", "YOUR_", "FILL_IN", "REPLACE_ME", "CHANGEME", "xoxb-", "<")


def _is_configured(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or v in {"null", "none", "TODO"}:
        return False
    if any(v.upper().startswith(p) or v.lower().startswith(p.lower()) for p in _PLACEHOLDERS):
        return False
    return True


_HTML_ENTITIES = (("&mdash;", "-"), ("&ndash;", "-"), ("&nbsp;", " "), ("&amp;", "&"))


def _strip_tags(text: str) -> str:
    """ntfy titles and the console are plain text, so strip any HTML we emit."""
    out = re.sub(r"<[^>]+>", "", text)
    for entity, replacement in _HTML_ENTITIES:
        out = out.replace(entity, replacement)
    return out.strip()


#: The plain-text version of a caption, for the plain-text channels.
def plain(text: str) -> str:
    return _strip_tags(text)


def _header_utf8(text: str) -> str:
    """Make `text` survive being sent as an HTTP header by urllib.

    http.client encodes header values as latin-1, which mangles anything
    outside latin-1 (and outright fails on emoji). Round-tripping through
    UTF-8 bytes then latin-1 puts the original UTF-8 on the wire, which is what
    ntfy and every other server expects.
    """
    return text.encode("utf-8").decode("latin-1")


# --------------------------------------------------------------- multipart


def _multipart_body(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    """Build a multipart/form-data body. Returns (body, content_type)."""
    boundary = "----RoomWatch" + uuid.uuid4().hex
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")

    for name, (filename, data, content_type) in files.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        chunks.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        chunks.append(data)
        chunks.append(b"\r\n")

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _post_json(url: str, payload: dict, timeout: int = 20) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "raw": raw[:400]}


def _post_multipart(url: str, fields: dict, files: dict, timeout: int = 30) -> dict:
    body, content_type = _multipart_body(fields, files)
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": content_type}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "raw": raw[:400]}


# ---------------------------------------------------------------------- ntfy


@dataclass
class NtfyNotifier:
    """The send_message / send_photo shape every channel implements."""

    server: str
    topic: str
    enabled: bool = True

    @property
    def ready(self) -> bool:
        return bool(self.enabled) and _is_configured(self.topic)

    def _post(self, title: str, body: bytes, content_type: str, filename: str | None = None,
              silent: bool = False) -> bool:
        if not self.ready:
            return False
        url = self.server.rstrip("/") + "/" + self.topic
        headers = {
            # ntfy stores header values verbatim, so this must NOT be
            # percent-encoded -- that would reach the phone as
            # "Alice%20is%20in%20your%20room".
            "Title": _header_utf8(title),
            "Tags": "camera",
            "Content-Type": content_type,
            # ntfy's own push toggle: 1 = push, 2 = no push, 3 = push silently.
            "Priority": "3" if silent else "1",
        }
        if filename:
            # The attachment name goes in the header (or a ?f= query param).
            # Putting it in the URL path makes ntfy treat it as a page name
            # and answer 404 {"error":"page not found"}.
            headers["Filename"] = filename
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=25) as resp:
                resp.read()
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.warning("ntfy post failed: %s", exc)
            return False

    def send_message(self, text: str, silent: bool = False) -> bool:
        first, _, rest = text.partition("\n")
        # Strip the body as well as the title: the event detail carries the
        # &mdash; separator, and ntfy has no HTML renderer, so it would reach
        # the phone as a literal "&mdash;".
        body = plain(rest.strip() or first)[:2000]
        return self._post(
            _strip_tags(first) or "RoomWatch",
            body.encode("utf-8"),
            "text/plain; charset=utf-8",
            silent=silent,
        )

    def send_photo(self, jpeg: bytes, caption: str, silent: bool = False, filename: str = "room.jpg") -> bool:
        """ntfy posts the image first, then the caption as a follow-up message."""
        first, _, rest = caption.partition("\n")
        title = _strip_tags(first) or "RoomWatch"
        if not self._post(title, jpeg, "image/jpeg", filename, silent=silent):
            return False
        body = plain(rest.strip() or first)[:2000]
        return self._post(title, body.encode("utf-8"), "text/plain; charset=utf-8", silent=silent)


# ------------------------------------------------------------------- webhook


@dataclass
class WebhookNotifier:
    """A plain multipart POST to a URL you control.

    Speaks the shape Discord and most webhook receivers accept: a `file` part
    carrying the JPEG and a `content` part carrying the caption. Use this if you
    would rather route alerts through something you already have. Slack is the
    exception -- it needs a publicly reachable image URL rather than an upload,
    so use ntfy for Slack.
    """

    url: str
    enabled: bool = True
    field_name: str = "file"
    extra_fields: dict = field(default_factory=dict)
    header_name: str = "X-Webhook-Secret"
    header_value: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.enabled) and _is_configured(self.url) and self.url.lower().startswith("http")

    def send_message(self, text: str, silent: bool = False) -> bool:
        if not self.ready:
            return False
        try:
            _post_multipart(
                self.url,
                {"content": _strip_tags(text)[:1900], **self.extra_fields},
                {},
                timeout=20,
            )
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.warning("webhook post failed: %s", exc)
            return False

    def send_photo(self, jpeg: bytes, caption: str, silent: bool = False,
                   filename: str = "room.jpg") -> bool:
        if not self.ready:
            return False
        headers = {}
        if _is_configured(self.header_value):
            headers = {self.header_name: self.header_value}
        try:
            body, content_type = _multipart_body(
                {"content": _strip_tags(caption)[:1900], **self.extra_fields},
                {self.field_name: (filename, jpeg, "image/jpeg")},
            )
            req = urllib.request.Request(
                self.url, data=body,
                headers={"Content-Type": content_type, **headers},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                resp.read()
            return True
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            log.warning("webhook photo post failed: %s", exc)
            return False



# ------------------------------------------------------------------- console


class ConsoleNotifier:
    """Prints alerts to the terminal, for setting up before a phone is wired up."""

    def __init__(self, enabled: bool = True, snapshot_dir: str = "snapshots") -> None:
        self.enabled = bool(enabled)
        self.snapshot_dir = snapshot_dir

    def send(self, title: str, message: str, jpeg: bytes | None = None, filename: str = "room.jpg") -> bool:
        if not self.enabled:
            return False
        stamp = time.strftime("%H:%M:%S")
        rule = "=" * 66
        print(f"\n{rule}\n[{stamp}] {plain(title)}\n{'-' * 66}\n{plain(message)}")
        if jpeg is not None:
            out = os.path.join(self.snapshot_dir, f"console_{int(time.time())}.jpg")
            try:
                os.makedirs(self.snapshot_dir, exist_ok=True)
                with open(out, "wb") as fh:
                    fh.write(jpeg)
                print(f"[photo saved] {out}")
            except OSError as exc:
                print(f"[photo save failed] {exc}")
        print(rule)
        return True


# ------------------------------------------------------------------ assembly


def build_phone_channels(cfg: dict) -> list:
    """Every enabled, correctly-filled phone channel, in config order.

    Kept in one place so the watcher and test_phone.py can never disagree about
    which channels are live.
    """
    channels: list = []

    ntfy_cfg = cfg.get("ntfy") or {}
    ntfy = NtfyNotifier(
        server=str(ntfy_cfg.get("server") or "https://ntfy.sh"),
        topic=str(ntfy_cfg.get("topic") or ""),
        enabled=bool(ntfy_cfg.get("enabled")),
    )
    if ntfy.ready:
        channels.append(ntfy)

    hook_cfg = cfg.get("webhook") or {}
    webhook = WebhookNotifier(
        url=str(hook_cfg.get("url") or ""),
        enabled=bool(hook_cfg.get("enabled")),
        field_name=str(hook_cfg.get("field_name") or "file"),
        extra_fields=hook_cfg.get("extra_fields") or {},
        header_name=str(hook_cfg.get("header_name") or "X-Webhook-Secret"),
        header_value=str(hook_cfg.get("header_value") or ""),
    )
    if webhook.ready:
        channels.append(webhook)

    return channels


def channel_setup_problems(cfg: dict) -> list[str]:
    """Plain-English reasons the phone link is not live yet, for error messages."""
    problems: list[str] = []
    if not any((cfg.get(k) or {}).get("enabled") for k in ("ntfy", "webhook")):
        problems.append('No channel is switched on. Set "enabled": true under ntfy '
                        "or webhook in config.json.")
        return problems
    if (cfg.get("ntfy") or {}).get("enabled"):
        if not _is_configured((cfg.get("ntfy") or {}).get("topic")):
            problems.append('ntfy is enabled but "topic" is still the placeholder. '
                            "Subscribe to that topic in the ntfy app on your phone first.")
    if (cfg.get("webhook") or {}).get("enabled"):
        if not _is_configured((cfg.get("webhook") or {}).get("url")):
            problems.append('webhook is enabled but "url" is still the placeholder.')
    return problems
