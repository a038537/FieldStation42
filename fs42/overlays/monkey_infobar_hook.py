# File: fs42/overlays/monkey_infobar_hook.py
# Purpose: Auto-feed the overlay without manual commands, by piggy-backing on
#          FS42's status publishing. It wraps station_player.update_status_socket
#          to extract Now/Next and emits an infobar event.
# Usage: add a single import near the top of field_player.py:
#   import fs42.overlays.monkey_infobar_hook  # noqa: F401

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

try:
    from fs42.overlays.bridge import send_infobar_event
    import fs42.station_player as _sp
except Exception:  # pragma: no cover
    # If FS42 is not available, do nothing (safe import)
    send_infobar_event = None  # type: ignore
    _sp = None  # type: ignore

if _sp is not None and hasattr(_sp, "update_status_socket") and send_infobar_event:
    _orig = _sp.update_status_socket

    def _extract(payload: Dict[str, Any]):
        # Try multiple shapes to be resilient to repo differences
        ch_num = (
            payload.get("channel_number")
            or payload.get("channelNum")
            or (payload.get("channel") or {}).get("number")
            or 0
        )
        ch_name = (
            payload.get("channel_name")
            or (payload.get("channel") or {}).get("name")
            or ""
        )
        now = payload.get("now") or payload.get("current") or payload.get("programme") or {}
        nxt = payload.get("next") or payload.get("up_next") or {}

        def to_dt(x):
            if not x:
                return None
            if isinstance(x, (int, float)):
                return datetime.utcfromtimestamp(x)
            if isinstance(x, str):
                try:
                    return datetime.fromisoformat(x)
                except Exception:
                    return None
            return None

        now_title = now.get("title") or now.get("name") or ""
        now_start = to_dt(now.get("start") or now.get("start_time"))
        now_end = to_dt(now.get("end") or now.get("end_time"))
        next_title = nxt.get("title") or nxt.get("name")
        next_start = to_dt(nxt.get("start") or nxt.get("start_time"))

        return int(ch_num), str(ch_name), str(now_title), now_start, now_end, next_title, next_start

    def update_status_socket(*args, **kwargs):  # type: ignore
        # Call original first to keep FS42 behaviour
        rv = _orig(*args, **kwargs)
        # Find the payload
        payload = None
        if args:
            for a in args:
                if isinstance(a, dict):
                    payload = a
                    break
        if payload is None:
            for v in kwargs.values():
                if isinstance(v, dict):
                    payload = v
                    break
        if payload:
            try:
                ch_num, ch_name, now_title, now_start, now_end, next_title, next_start = _extract(payload)
                if ch_name and now_title:
                    send_infobar_event(ch_num, ch_name, now_title, now_start, now_end, next_title, next_start)
            except Exception:
                pass
        return rv

    # Patch it
    _sp.update_status_socket = update_status_socket  # type: ignore
