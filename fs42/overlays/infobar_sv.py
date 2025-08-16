# File: fs42/overlays/infobar_sv.py
# Purpose: Always-on-top transparent overlay that shows a Swedish-style (English labels)
#          channel-change infobar (as in the provided photo) whenever an
#          event JSON is received (UDP) or a watched file updates.
#          Now/Next is supported automatically via the monkey patch below.
#
# Dependencies (install on Pi):
#   pip install glfw PyOpenGL Pillow
#   sudo apt-get install -y libglfw3 fonts-dejavu-core
#
# How it works
# ------------
# - Listens on UDP 127.0.0.1:42424 for JSON payloads describing the
#   channel & program currently tuned.
# - Also watches a file at runtime/infobar_event.json. When it changes,
#   the bar animates in, stays visible for a few seconds, then fades out.
# - Designed for PAL 720x576 but responsive to any resolution; it uses
#   safe-area scaling so it looks correct at 720p/1080p too.
#
# Event JSON shape (either via UDP or file):
#   {
#     "ts": 1734111111,                 # unix seconds
#     "channel_number": 3,
#     "channel_name": "TV3 Stockholm",
#     "title": "Mamma Mia!",           # NOW
#     "start": "2025-08-16T20:00:00",
#     "end":   "2025-08-16T22:18:00",
#     "next_title": "Nyheterna",       # optional NEXT
#     "next_start": "2025-08-16T22:18:00"
#   }
#
# Start:
#   DISPLAY=:0 python3 -m fs42.overlays.infobar_sv

from __future__ import annotations

import json
import math
import os
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

# Third-party
import glfw  # type: ignore
from OpenGL.GL import *  # type: ignore
from PIL import Image, ImageDraw, ImageFont

# ------------------------------- Config ------------------------------------
UDP_PORT = 42424
UDP_ADDR = ("127.0.0.1", UDP_PORT)
EVENT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "runtime", "infobar_event.json")
VIS_SECONDS = 5.0
FADE_IN = 0.18
FADE_OUT = 0.28
SAFE_MARGIN = 0.05  # 5% inset

# Colors sampled/approximated from the reference UI
COL_HEADER = (210, 165, 92, 255)    # ochre/orange strip
COL_HEADER_TEXT = (15, 15, 15, 255)
COL_BODY = (12, 68, 150, 238)       # deep blue with slight transparency
COL_TITLE = (255, 255, 255, 255)
COL_SUB = (210, 230, 255, 255)      # light blue text for the remaining time
COL_MENU = (230, 240, 255, 255)
COL_DIVIDER = (0, 0, 0, 120)
COL_NEXT = (255, 255, 255, 230)
COL_NEXT_LABEL = (220, 235, 255, 255)

FONT_REG = "DejaVuSans.ttf"
FONT_BOLD = "DejaVuSans-Bold.ttf"

# ------------------------------- Data --------------------------------------
@dataclass
class InfobarEvent:
    channel_number: int
    channel_name: str
    title: str
    start: datetime
    end: datetime
    ts: float
    next_title: str | None = None
    next_start: datetime | None = None

    @staticmethod
    def from_json(d: dict) -> "InfobarEvent":
        # Parse ISO times (assume UTC if naive)
        def parse_iso(s: str | None) -> datetime | None:
            if not s:
                return None
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        return InfobarEvent(
            channel_number=int(d.get("channel_number", 0)),
            channel_name=str(d.get("channel_name", "")),
            title=str(d.get("title", "")),
            start=parse_iso(d.get("start", None)) or datetime.now(timezone.utc),
            end=parse_iso(d.get("end", None)) or datetime.now(timezone.utc),
            ts=float(d.get("ts", time.time())),
            next_title=(str(d.get("next_title")) if d.get("next_title") else None),
            next_start=parse_iso(d.get("next_start")) if d.get("next_start") else None,
        )

# --------------------------- Texture helper --------------------------------
class Texture:
    def __init__(self):
        self.tex_id = glGenTextures(1)
        self.w = 0
        self.h = 0

    def upload_pil(self, img: Image.Image):
        self.w, self.h = img.size
        data = img.tobytes("raw", "RGBA", 0, -1)
        glBindTexture(GL_TEXTURE_2D, self.tex_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, self.w, self.h, 0, GL_RGBA, GL_UNSIGNED_BYTE, data)
        glBindTexture(GL_TEXTURE_2D, 0)

# ----------------------------- Renderer ------------------------------------
class InfoBarRenderer:
    def __init__(self, W: int, H: int):
        self.W = W
        self.H = H
        self.safe_x = int(W * SAFE_MARGIN)
        self.safe_y = int(H * SAFE_MARGIN)
        self.safe_W = W - self.safe_x * 2
        self.safe_H = H - self.safe_y * 2

        # Baseline metrics use PAL width 720
        self.scale = self.safe_W / 720.0
        self.header_h = int(42 * self.scale)
        self.body_h = int(148 * self.scale)  # a bit taller for NEXT row
        self.total_h = self.header_h + self.body_h

        self.font_title = ImageFont.truetype(FONT_BOLD, max(22, int(34 * self.scale)))
        self.font_header = ImageFont.truetype(FONT_BOLD, max(14, int(22 * self.scale)))
        self.font_sub = ImageFont.truetype(FONT_REG, max(12, int(18 * self.scale)))
        self.font_menu = ImageFont.truetype(FONT_REG, max(11, int(16 * self.scale)))
        self.font_next_label = ImageFont.truetype(FONT_REG, max(11, int(16 * self.scale)))
        self.font_next = ImageFont.truetype(FONT_BOLD, max(14, int(20 * self.scale)))

        self.tex = Texture()
        self.current_img_key = None  # cache key to avoid re-render unless text changes

    @staticmethod
    def fmt_remaining(now_utc: datetime, end_utc: datetime) -> str:
        secs = int((end_utc - now_utc).total_seconds())
        if secs < 0:
            secs = 0
        h = secs // 3600
        m = (secs % 3600) // 60
        return f"Remaining time {h:02d}:{m:02d} Hours"

    def _draw_infobar_image(self, ev: InfobarEvent) -> Image.Image:
        img = Image.new("RGBA", (self.safe_W, self.total_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        # Header background
        d.rectangle([0, 0, self.safe_W, self.header_h], fill=COL_HEADER)
        d.line([(0, 1), (self.safe_W, 1)], fill=(255, 255, 255, 40), width=1)
        d.line([(0, self.header_h - 2), (self.safe_W, self.header_h - 2)], fill=COL_DIVIDER, width=2)

        # Header text: number + channel name
        pad = int(12 * self.scale)
        ch_str = f"{ev.channel_number}"
        d.text((pad, int(self.header_h * 0.18)), ch_str, font=self.font_header, fill=COL_HEADER_TEXT)
        n_w = d.textlength(ch_str, font=self.font_header)
        d.text((pad + n_w + int(16 * self.scale), int(self.header_h * 0.18)), ev.channel_name, font=self.font_header, fill=COL_HEADER_TEXT)

        glyph = "-:-:-"
        g_w = d.textlength(glyph, font=self.font_header)
        d.text((self.safe_W - g_w - pad, int(self.header_h * 0.18)), glyph, font=self.font_header, fill=COL_HEADER_TEXT)

        # Body background (deep blue)
        d.rectangle([0, self.header_h, self.safe_W, self.total_h], fill=COL_BODY)

        # Title (white, bold)
        title_y = self.header_h + int(12 * self.scale)
        d.text((pad, title_y), ev.title, font=self.font_title, fill=COL_TITLE)

        # Remaining time
        now_utc = datetime.now(timezone.utc)
        rem = self.fmt_remaining(now_utc, ev.end)
        rem_y = title_y + int(self.font_title.size * 1.3)
        d.text((pad, rem_y), rem, font=self.font_sub, fill=COL_SUB)

        # NEXT row (label + bold title + start hh:mm)
        if ev.next_title:
            next_y = rem_y + int(self.font_sub.size * 1.25)
            d.text((pad, next_y), "Next:", font=self.font_next_label, fill=COL_NEXT_LABEL)
            label_w = d.textlength("Next:", font=self.font_next_label)
            text = ev.next_title
            if ev.next_start:
                hhmm = ev.next_start.astimezone(timezone.utc).strftime("%H:%M")
                text = f"{text}  {hhmm}"
            d.text((pad + int(label_w + 12 * self.scale), next_y - int(2 * self.scale)), text, font=self.font_next, fill=COL_NEXT)

        # Menu row with an "i" circle and bullets
        menu_y = self.total_h - int(10 * self.scale) - self.font_menu.size
        circ_r = int(self.font_menu.size * 0.75)
        cx = pad + circ_r
        cy = menu_y + self.font_menu.size // 2
        d.ellipse([cx - circ_r, cy - circ_r, cx + circ_r, cy + circ_r], outline=COL_MENU, width=max(2, int(2 * self.scale)))
        d.text((cx - self.font_menu.size * 0.33, menu_y - int(2 * self.scale)), "i", font=self.font_menu, fill=COL_MENU)

        text1 = "Information"
        t1_x = pad + circ_r * 2 + int(8 * self.scale)
        d.text((t1_x, menu_y), text1, font=self.font_menu, fill=COL_MENU)
        b1_x = t1_x + int(d.textlength(text1, font=self.font_menu)) + int(18 * self.scale)
        d.text((b1_x, menu_y), "•", font=self.font_menu, fill=COL_MENU)
        text2 = "Search by time"
        t2_x = b1_x + int(d.textlength("•", font=self.font_menu)) + int(18 * self.scale)
        d.text((t2_x, menu_y), text2, font=self.font_menu, fill=COL_MENU)
        b2_x = t2_x + int(d.textlength(text2, font=self.font_menu)) + int(18 * self.scale)
        d.text((b2_x, menu_y), "•", font=self.font_menu, fill=COL_MENU)
        text3 = "Search by channel"
        t3_x = b2_x + int(d.textlength("•", font=self.font_menu)) + int(18 * self.scale)
        d.text((t3_x, menu_y), text3, font=self.font_menu, fill=COL_MENU)

        return img

    def ensure_texture(self, ev: InfobarEvent):
        key = (ev.channel_number, ev.channel_name, ev.title, int(ev.end.timestamp() if ev.end else 0), ev.next_title, int(ev.next_start.timestamp() if ev.next_start else 0))
        if key != self.current_img_key:
            img = self._draw_infobar_image(ev)
            self.tex.upload_pil(img)
            self.current_img_key = key

    def draw(self, alpha: float):
        x = self.safe_x
        y = self.H - self.safe_y - self.total_h
        w = self.safe_W
        h = self.total_h

        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self.tex.tex_id)
        glColor4f(1.0, 1.0, 1.0, alpha)

        glBegin(GL_QUADS)
        glTexCoord2f(0.0, 1.0); glVertex2f(x, y)
        glTexCoord2f(1.0, 1.0); glVertex2f(x + w, y)
        glTexCoord2f(1.0, 0.0); glVertex2f(x + w, y + h)
        glTexCoord2f(0.0, 0.0); glVertex2f(x, y + h)
        glEnd()

        glBindTexture(GL_TEXTURE_2D, 0)
        glDisable(GL_TEXTURE_2D)

# ---------------------------- Main overlay ---------------------------------
class OverlayApp:
    def __init__(self):
        if not glfw.init():
            print("Failed to initialize GLFW")
            sys.exit(1)

        monitor = glfw.get_primary_monitor()
        mode = glfw.get_video_mode(monitor)
        W, H = mode.size.width, mode.size.height

        glfw.window_hint(glfw.DECORATED, glfw.FALSE)
        glfw.window_hint(glfw.TRANSPARENT_FRAMEBUFFER, glfw.TRUE)
        glfw.window_hint(glfw.FLOATING, glfw.TRUE)
        glfw.window_hint(glfw.FOCUS_ON_SHOW, glfw.FALSE)

        self.win = glfw.create_window(W, H, "FS42-InfoBar", None, None)
        if not self.win:
            glfw.terminate()
            print("Failed to create window")
            sys.exit(2)
        glfw.make_context_current(self.win)
        glfw.swap_interval(0)
        glfw.set_window_pos(self.win, 0, 0)

        glViewport(0, 0, W, H)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(0, W, H, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glDisable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        self.renderer = InfoBarRenderer(W, H)
        self.visible_until = 0.0
        self.fade_state = "idle"  # idle | in | hold | out
        self.fade_t0 = 0.0
        self.event: InfobarEvent | None = None

        self._udp_thread = threading.Thread(target=self._udp_listener, daemon=True)
        self._udp_thread.start()
        self._file_mtime = 0.0

    def _udp_listener(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(UDP_ADDR)
        while True:
            try:
                data, _ = s.recvfrom(65535)
                payload = json.loads(data.decode("utf-8"))
                self._on_event(InfobarEvent.from_json(payload))
            except Exception:
                pass

    def _poll_file(self):
        try:
            st = os.stat(EVENT_FILE)
            if st.st_mtime > self._file_mtime:
                self._file_mtime = st.st_mtime
                with open(EVENT_FILE, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                self._on_event(InfobarEvent.from_json(payload))
        except FileNotFoundError:
            pass
        except Exception:
            pass

    def _on_event(self, ev: InfobarEvent):
        self.event = ev
        self.renderer.ensure_texture(ev)
        self.fade_state = "in"
        self.fade_t0 = time.time()
        self.visible_until = self.fade_t0 + FADE_IN + VIS_SECONDS + FADE_OUT

    def run(self):
        while not glfw.window_should_close(self.win):
            self._poll_file()

            glClearColor(0, 0, 0, 0)
            glClear(GL_COLOR_BUFFER_BIT)

            alpha = 0.0
            now = time.time()
            if self.event is not None and now < self.visible_until:
                t = now - self.fade_t0
                if t < FADE_IN:
                    self.fade_state = "in"
                    alpha = t / FADE_IN
                elif t < FADE_IN + VIS_SECONDS:
                    self.fade_state = "hold"
                    alpha = 1.0
                else:
                    self.fade_state = "out"
                    t2 = t - (FADE_IN + VIS_SECONDS)
                    alpha = max(0.0, 1.0 - (t2 / FADE_OUT))

                self.renderer.draw(alpha)

            glfw.swap_buffers(self.win)
            glfw.poll_events()
            time.sleep(1 / 60.0)

        glfw.terminate()


def main():
    try:
        app = OverlayApp()
        app.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
