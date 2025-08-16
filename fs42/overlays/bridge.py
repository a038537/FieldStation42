# File: fs42/overlays/bridge.py
# Purpose: Tiny helper used from anywhere in FS42 to trigger the infobar.
#          You can import send_infobar_event and call it after a channel
#          change or program change. It writes the watched file AND
#          emits a UDP packet to wake the overlay immediately.

import json
import os
import socket
import time
from datetime import datetime
from typing import Optional

EVENT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "runtime", "infobar_event.json")
UDP_ADDR = ("127.0.0.1", 42424)


def _iso(dt: Optional[datetime]) -> str:
    if dt is None:
        dt = datetime.utcnow()
    return dt.replace(microsecond=0).isoformat()


def send_infobar_event(
    channel_number: int,
    channel_name: str,
    title: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    next_title: Optional[str] = None,
    next_start: Optional[datetime] = None,
):
    payload = {
        "ts": time.time(),
        "channel_number": int(channel_number),
        "channel_name": str(channel_name),
        "title": str(title),
        "start": _iso(start),
        "end": _iso(end),
    }
    if next_title:
        payload["next_title"] = str(next_title)
    if next_start:
        payload["next_start"] = _iso(next_start)

    os.makedirs(os.path.dirname(EVENT_FILE), exist_ok=True)
    with open(EVENT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(json.dumps(payload).encode("utf-8"), UDP_ADDR)
        s.close()
    except OSError:
        pass
