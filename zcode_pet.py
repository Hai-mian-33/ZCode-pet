# -*- coding: utf-8 -*-
# Author: Sun Haiming (孙海铭) · https://github.com/Hai-mian-33
"""
ZCode Pet — a task-progress desktop pet for ZCode (bilingual UI: 中文 / English)
================================================================================
Data source : ZCode official hooks (SessionStart / UserPromptSubmit / PreToolUse /
              PermissionRequest / PostToolUse / PostToolUseFailure / Stop)
              → curl POST 127.0.0.1:<PORT>/event → the pet renders live progress.
              Both snake_case and camelCase payload styles are supported.
Lifecycle   : the pet appears when ZCode starts (hook launcher) and exits when
              the ZCode process closes (built-in process monitor).
Sessions    : every session gets its own state; cards are aggregated by priority
              (waiting > executing > thinking > done) so they never overwrite
              each other. Remote sessions are tagged.
Tunnels     : the pet keeps SSH reverse tunnels (tunnels.txt) alive so remote
              events can reach it.
Visual      : ZCode brand tokens (#FEBC2E amber / #161616 dark), single-stroke
              rounded "Z" character.
Usage       : python zcode_pet.py [--demo] [--selftest] [--port 8765]
Language    : PET_LANG=zh|en env var, right-click menu switch, persisted.
Dependency  : pip install PyQt5
"""
import sys
import os
import json
import math
import time
import html
import random
import argparse
import threading
import tempfile
import subprocess

try:
    from PyQt5.QtCore import (Qt, QTimer, pyqtSignal, QObject, QPoint, QPointF,
                              QRectF, QSettings, QLockFile, QAbstractNativeEventFilter,
                              QLocale)
    from PyQt5.QtGui import (QPainter, QColor, QBrush, QPen, QLinearGradient, QFont,
                             QPainterPath, QIcon, QPixmap, QGuiApplication, QPolygonF)
    from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout,
                                 QHBoxLayout, QMenu, QSystemTrayIcon, QFrame,
                                 QGraphicsDropShadowEffect)
except ImportError:
    print("Please install the dependency first: pip install PyQt5 / 请先安装依赖: pip install PyQt5")
    sys.exit(1)

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ========== ZCode brand tokens (do not change) ==========
BRAND     = "#FEBC2E"
BG        = "#161616"
BORDER    = "#2A2A2A"
FG        = "#FFFFFF"
FG_SUBTLE = "#A1A1A1"
SUCCESS   = "#4ADE80"
DESTRUCT  = "#EF4444"
WARNING   = "#FBBF24"
WAITING   = "#F97316"
RAW_DIM   = "#6B6B6B"

DEFAULT_PORT = 8765
PET_SIZE     = (140, 148)
BUBBLE_W     = 300
BUBBLE_H     = 72
DONE_WINDOW  = 8.0
LOG_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pet.log")
TUNNELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tunnels.txt")

PRIORITY = {"waiting": 0, "executing": 1, "thinking": 2, "done": 3, "idle": 4}

# ========== Bilingual strings (中文 / English) ==========
I18N = {
    "zh": {
        "chip_thinking":  "◐思考中",
        "chip_exec":      "▸{tool}·{steps}步·{t}",
        "chip_wait":      "⏸确认{tool}",
        "chip_done":      "✓{steps}步·{t}",
        "chip_idle":      "●待命",
        "full_thinking":  "◐ {w}thinking · 分析任务…",
        "full_exec":      "▸ {w}{tool} · {steps}步 · {t}",
        "full_wait":      "⏸ {w}等你确认 {tool}",
        "full_done":      "✓ {w}done · {steps}步 · {t}",
        "full_idle":      "● idle · 待命",
        "card_untitled":  "（未命名任务）",
        "card_done_res":  "{w}已完成 · {result}",
        "card_done":      "{w}已完成 · {steps}步 · {t}",
        "card_exec":      "{w}正在执行 · {tool}",
        "card_wait":      "{w}等你确认 · {tool}",
        "card_think":     "{w}分析任务…",
        "card_idle":      "{w}空闲",
        "where_remote":   "远程 · ",
        "bubble_ready":   "就绪 (•̀ᴗ•́)و",
        "bubble_go":      "开跑 (๑•̀ㅂ•́)و",
        "bubble_done":    "搞定！✓",
        "bubble_wait":    "等你确认 (・_・;)",
        "bubble_zzz":     "待命 zzz",
        "menu_showhide":  "显示 / 隐藏",
        "menu_fold":      "气泡：折叠/展开",
        "menu_hide":      "隐藏任务卡片",
        "menu_cards":     "显示任务卡片",
        "menu_demo":      "演示一轮任务",
        "menu_reset":     "重置位置",
        "menu_lang":      "Language / 语言",
        "menu_quit":      "退出",
        "tray_tip":       "ZCode Pet — 任务进度桌宠",
        "log_join":       "会话接入 #{sid}（{where}，共 {n} 个）",
        "log_ready":      "会话就绪（{src}）",
        "log_new_task":   "{w}新任务：{t}",
        "log_tool_done":  "{w}✓ {tool} 完成 · 第 {n} 步",
        "log_tool_fail":  "✗ {tool} 失败 {e}",
        "log_perm":       "⏸ 等待权限确认：{tool} {snip}",
        "log_stop":       "{w}★ 任务完成 · {n} 步 · 用时 {t}",
        "log_jump":       "跳转 ZCode 窗口 {ok}",
        "log_jump_card":  "点击卡片跳转 ZCode（会话 #{sid}{r}）{ok}",
        "log_card_ack":   "卡片已查看并移除（会话 #{sid}）",
        "log_lc_seen":    "已检测到 ZCode 进程 ✓（ZCode 关闭时桌宠将随之退出）",
        "log_lc_exit":    "ZCode 进程已关闭 → 桌宠随之退出",
        "log_tunnel_up":  "隧道已建立 → {t}",
        "log_tunnel_dn":  "隧道断开，准备重试：{t}",
        "log_tunnel_rm":  "隧道已移除：{t}",
        "log_tunnel_err": "隧道启动失败 {t}: {e}",
        "log_empty":      "收到空事件（钩子未传 stdin，请检查远程钩子版本）",
        "log_unknown":    "未知事件 {n}",
        "log_lang":       "语言已切换 → 中文",
        "log_err":        "处理 {n} 出错: {e}",
        "where_log":      "远程",
        "local_log":      "本机",
        "ok_log":         "✓",
        "ok_miss":        "（未找到窗口）",
        "remote_log":     "·远程",
        "untitled_log":   "（未命名任务）",
        "demo_prompt":    "修复登录页在 1080p 下的按钮折行问题，并补充回归测试",
        "lang_name":      "中文",
    },
    "en": {
        "chip_thinking":  "◐Thinking",
        "chip_exec":      "▸{tool}·{steps}·{t}",
        "chip_wait":      "⏸Confirm {tool}",
        "chip_done":      "✓{steps}·{t}",
        "chip_idle":      "●Idle",
        "full_thinking":  "◐ {w}Thinking · analyzing…",
        "full_exec":      "▸ {w}{tool} · {steps} steps · {t}",
        "full_wait":      "⏸ {w}Waiting for you: {tool}",
        "full_done":      "✓ {w}Done · {steps} steps · {t}",
        "full_idle":      "● idle · standby",
        "card_untitled":  "(Untitled task)",
        "card_done_res":  "{w}Completed · {result}",
        "card_done":      "{w}Completed · {steps} steps · {t}",
        "card_exec":      "{w}Running · {tool}",
        "card_wait":      "{w}Waiting · {tool}",
        "card_think":     "{w}Analyzing…",
        "card_idle":      "{w}Idle",
        "where_remote":   "Remote · ",
        "bubble_ready":   "Ready (•̀ᴗ•́)و",
        "bubble_go":      "Go! (๑•̀ㅂ•́)و",
        "bubble_done":    "Done! ✓",
        "bubble_wait":    "ZCode needs you (・_・;)",
        "bubble_zzz":     "zzz",
        "menu_showhide":  "Show / Hide",
        "menu_fold":      "Cards: Fold / Expand",
        "menu_hide":      "Hide cards",
        "menu_cards":     "Show cards",
        "menu_demo":      "Demo task",
        "menu_reset":     "Reset position",
        "menu_lang":      "Language / 语言",
        "menu_quit":      "Quit",
        "tray_tip":       "ZCode Pet — task progress desktop pet",
        "log_join":       "Session joined #{sid} ({where}, {n} total)",
        "log_ready":      "Session ready ({src})",
        "log_new_task":   "{w}New task: {t}",
        "log_tool_done":  "{w}✓ {tool} done · step {n}",
        "log_tool_fail":  "✗ {tool} failed {e}",
        "log_perm":       "⏸ Permission needed: {tool} {snip}",
        "log_stop":       "{w}★ Task done · {n} steps · {t}",
        "log_jump":       "Jump to ZCode window {ok}",
        "log_jump_card":  "Card clicked → ZCode (session #{sid}{r}) {ok}",
        "log_card_ack":   "Card viewed & removed (session #{sid})",
        "log_lc_seen":    "ZCode process detected ✓ (pet exits when ZCode closes)",
        "log_lc_exit":    "ZCode closed → pet exits",
        "log_tunnel_up":  "Tunnel up → {t}",
        "log_tunnel_dn":  "Tunnel down, retrying: {t}",
        "log_tunnel_rm":  "Tunnel removed: {t}",
        "log_tunnel_err": "Tunnel failed {t}: {e}",
        "log_empty":      "Empty event (hook sent no stdin)",
        "log_unknown":    "Unknown event {n}",
        "log_lang":       "Language → English",
        "log_err":        "Error handling {n}: {e}",
        "where_log":      "Remote",
        "local_log":      "Local",
        "ok_log":         "✓",
        "ok_miss":        "(window not found)",
        "remote_log":     "·Remote",
        "untitled_log":   "(Untitled task)",
        "demo_prompt":    "Fix the login page button wrapping at 1080p and add regression tests",
        "lang_name":      "English",
    },
}

CURRENT_LANG = "zh"


def T(key, **kw):
    table = I18N.get(CURRENT_LANG) or I18N["zh"]
    s = table.get(key)
    if s is None:
        s = I18N["zh"].get(key, key)
    return s.format(**kw) if kw else s


def set_lang(lang):
    global CURRENT_LANG
    CURRENT_LANG = lang if lang in I18N else "zh"
    QSettings("ZCode", "ZCodePet").setValue("lang", CURRENT_LANG)


def _detect_lang():
    env = os.environ.get("PET_LANG")
    if env in I18N:
        return env
    saved = str(QSettings("ZCode", "ZCodePet").value("lang", "") or "")
    if saved in I18N:
        return saved
    return "zh" if QLocale.system().name().startswith("zh") else "en"


# ========== Event server: hooks → curl POST → Qt main thread ==========
class _Bridge(QObject):
    event_received = pyqtSignal(dict)


BRIDGE = _Bridge()


class _EventHandler(BaseHTTPRequestHandler):
    server_version = "ZCodePet/1.0"

    def do_POST(self):
        if self.path.rstrip("/") not in ("/event", "/events"):
            self.send_response(404)
            self.end_headers()
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n > 0 else b""
            payload = json.loads(raw.decode("utf-8", "replace")) if raw else {}
            if not isinstance(payload, dict):
                payload = {"raw": str(payload)}
        except Exception as e:
            payload = {"parse_error": str(e)}
        # 204 with no body: hook stdout must stay empty (strict JSON validation)
        try:
            self.send_response(204)
            self.end_headers()
        except Exception:
            pass
        BRIDGE.event_received.emit(payload)

    def do_GET(self):
        body = b"zcode-pet ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class EventServer:
    """Local HTTP server; uses the first free port starting at the preferred one."""

    def __init__(self, preferred_port):
        self.httpd = None
        self.port = None
        self.thread = None
        self.preferred = preferred_port

    def start(self):
        for p in range(self.preferred, self.preferred + 11):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", p), _EventHandler)
                self.port = p
                break
            except OSError:
                continue
        if self.httpd is None:
            return False
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       kwargs={"poll_interval": 0.2}, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass


# ========== Persistent tunnels: the pet keeps its own SSH reverse tunnels ==========
class TunnelManager(QObject):
    """Reads tunnels.txt (one user@host[:port] per line) and maintains
    `ssh -N -R 8765:127.0.0.1:8765` per server. Exited processes are retried
    every cycle; coexists with the user's own SSH sessions (first binder wins)."""

    def __init__(self, log_fn, interval_s=60):
        super().__init__()
        self._log = log_fn
        self.procs = {}
        self.timer = QTimer(self, interval=interval_s * 1000, timeout=self.ensure)
        self.timer.start()
        self.ensure()

    @staticmethod
    def _targets():
        try:
            with open(TUNNELS_PATH, "r", encoding="utf-8") as f:
                return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
        except Exception:
            return []

    @staticmethod
    def _cmd(target):
        if ":" in target:
            target, port = target.rsplit(":", 1)
        else:
            port = "22"
        return ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=60",
                "-o", "ServerAliveCountMax=3", "-p", port, target]

    def ensure(self):
        targets = self._targets()
        for key in list(self.procs):
            p = self.procs[key]
            if key not in targets:
                if p.poll() is None:
                    p.terminate()
                del self.procs[key]
                self._log("tunnel", T("log_tunnel_rm", t=key))
            elif p.poll() is not None:
                del self.procs[key]
                self._log("tunnel", T("log_tunnel_dn", t=key))
        for t in targets:
            if self.procs.get(t) is None or self.procs[t].poll() is not None:
                try:
                    kwargs = {}
                    if os.name == "nt":
                        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
                    p = subprocess.Popen(self._cmd(t), stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, **kwargs)
                    self.procs[t] = p
                    self._log("tunnel", T("log_tunnel_up", t=t))
                except Exception as e:
                    self._log("tunnel", T("log_tunnel_err", t=t, e=e))

    def status(self):
        total = len(self._targets())
        up = sum(1 for p in self.procs.values() if p.poll() is None)
        return up, total

    def stop_all(self):
        for p in self.procs.values():
            try:
                if p.poll() is None:
                    p.terminate()
            except Exception:
                pass
        self.procs.clear()


# ========== Global hotkey Ctrl+Alt+Z (Windows) ==========
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL = 0x1, 0x2
VK_Z = 0x5A
HOTKEY_ID = 0x5A50


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, callback):
        super().__init__()
        self._cb = callback

    def nativeEventFilter(self, eventType, message):
        try:
            if eventType == b"windows_generic_MSG":
                import ctypes.wintypes as wt
                msg = wt.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self._cb()
                    return True, 0
        except Exception:
            pass
        return False, 0


# ========== Core: the anthropomorphized ZCode "Z" ==========
class ZLogoFace(QWidget):
    """The bold ZCode "Z" logo turned into a pet: eyes / mouth / thinking
    bubbles / status lamp. The Z is a single three-segment polyline stroke
    with round joins so the strokes connect smoothly."""

    MOOD_LAMP = {"idle": FG_SUBTLE, "thinking": WARNING, "executing": BRAND,
                 "waiting": WAITING, "done": SUCCESS}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mood = "idle"
        self.blink = False
        self.tick = 0
        self.look = [0, 0]
        self._shake = 0
        QTimer(self, timeout=self._do_blink, interval=random.randint(2800, 4600)).start()
        QTimer(self, timeout=self._wander, interval=2600).start()
        self._anim = QTimer(self, timeout=self._step, interval=90)
        self._anim.start()

    def _do_blink(self):
        if self.mood == "done":
            return
        self.blink = True
        self.update()
        QTimer.singleShot(140, self._end_blink)

    def _end_blink(self):
        self.blink = False
        self.update()

    def _wander(self):
        if self.mood == "idle":
            self.look = [random.randint(-3, 3), random.randint(-2, 2)]
            self.update()

    def _step(self):
        self.tick += 1
        if self._shake > 0:
            self._shake = max(0, self._shake - 0.5)
        self.update()

    def set_mood(self, m):
        self.mood = m
        self.blink = False
        self.look = [0, 0]
        self.update()

    def shake(self):
        self._shake = 8

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = float(self.width()), float(self.height())
        cx = w / 2
        t = self.tick

        dy = 0.0
        dx = math.sin(t * 1.7) * self._shake * 0.7 if self._shake > 0 else 0.0
        if self.mood == "executing":
            dy = abs(math.sin(t / 2.5)) * -2.0
        elif self.mood == "waiting":
            dy = abs(math.sin(t / 1.8)) * -2.5
        elif self.mood == "thinking":
            dy = math.sin(t / 6.0) * 1.2

        z_w, z_h = w * 0.50, h * 0.72
        z_x = cx - z_w / 2 + dx
        z_y = (h - z_h) / 2 + 6 + dy
        bar = z_h * 0.26

        # shadow layer (slightly thicker, doubles as soft outline)
        self._draw_z(p, z_x + 1.5, z_y + 2.5, z_w, z_h, QColor(0, 0, 0, 130), pad=2.0)

        # body (brand gradient, single stroke)
        grad = QLinearGradient(z_x, z_y, z_x, z_y + z_h)
        grad.setColorAt(0.0, QColor("#FFD666"))
        grad.setColorAt(1.0, QColor(BRAND))
        self._draw_z(p, z_x, z_y, z_w, z_h, QBrush(grad))

        # ---- eyes (center of the top bar) ----
        eye_y = z_y + bar * 0.52
        eye_dx = z_w * 0.20
        for side in (-1, 1):
            ex = cx + dx + side * eye_dx + self.look[0]
            ey = eye_y + self.look[1]
            if self.mood == "done":
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor("#1A1A1A"), 2.2, Qt.SolidLine, Qt.RoundCap))
                p.drawArc(QRectF(ex - 5, ey - 3.5, 10, 8), 20 * 16, 140 * 16)
            elif self.blink:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor("#1A1A1A"), 2.2, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(QPointF(ex - 5, ey), QPointF(ex + 5, ey))
            else:
                ry = 6.0 if self.mood == "waiting" else 5.4
                if self.mood == "executing":
                    ry = 4.8
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#1A1A1A"))
                p.drawEllipse(QPointF(ex, ey), 5.2, ry)
                p.setBrush(QColor("white"))
                p.drawEllipse(QPointF(ex + 1.7, ey - 1.9), 1.7, 1.7)

        # ---- mouth (midpoint of the diagonal stroke) ----
        mouth_x = cx + dx
        mouth_y = z_y + z_h * 0.50
        p.setPen(QPen(QColor("#1A1A1A"), 2.2, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        if self.mood == "waiting":
            p.setBrush(QColor("#8B4513"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(mouth_x, mouth_y), 4.0, 5.0)
        elif self.mood == "executing":
            p.setBrush(QColor("#8B4513"))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(mouth_x, mouth_y), 3.2, 3.8)
        elif self.mood == "done":
            p.drawArc(QRectF(mouth_x - 7, mouth_y - 3.5, 14, 10), 200 * 16, 140 * 16)
        elif self.mood == "thinking":
            p.drawLine(QPointF(mouth_x - 4.5, mouth_y), QPointF(mouth_x + 4.5, mouth_y))
        else:
            p.drawArc(QRectF(mouth_x - 6, mouth_y - 2.5, 12, 9), 200 * 16, 140 * 16)

        # ---- thinking: "Z" bubbles above the head ----
        if self.mood == "thinking":
            rise = (t // 4) % 3 * 1.5
            for i in range(3):
                bx = cx + (i - 1) * 19
                by = z_y - 5 - i * 4 - rise
                alpha = [110, 180, 255][(t // 3 + i) % 3]
                c = QColor(BRAND)
                c.setAlpha(alpha)
                p.setBrush(c)
                p.setPen(QPen(c))
                fz = 8 + i * 3
                p.setFont(QFont("Arial", int(fz), QFont.Black))
                p.drawText(QPointF(bx - fz / 2, by), "Z")

        # ---- waiting: "!" bubble ----
        if self.mood == "waiting":
            bx, by = cx + dx + z_w * 0.68, z_y - 7
            pulse = 170 + int(85 * abs(math.sin(t / 2)))
            glow = QColor(WAITING)
            glow.setAlpha(60)
            p.setBrush(glow)
            p.drawEllipse(QPointF(bx, by), 11.5, 11.5)
            c = QColor(WAITING)
            c.setAlpha(pulse)
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(bx, by), 8.5, 8.5)
            p.setBrush(QColor("#FFFFFF"))
            p.setPen(QPen(QColor("#FFFFFF")))
            p.setFont(QFont("Arial", 10, QFont.Black))
            p.drawText(QPointF(bx - 2.1, by + 4.2), "!")

        # ---- done: sparkles ----
        if self.mood == "done":
            for (fx, fy) in ((z_x - 6, z_y + 4), (z_x + z_w + 6, z_y + 10),
                             (z_x + 2, z_y + z_h + 4), (z_x + z_w - 4, z_y + z_h - 2)):
                a = 120 + int(120 * abs(math.sin((t + fx) / 3)))
                c = QColor(SUCCESS)
                c.setAlpha(a)
                p.setBrush(c)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(fx, fy), 2.2, 2.2)

        # ---- status lamp (bottom) ----
        lamp = QColor(self.MOOD_LAMP.get(self.mood, FG_SUBTLE))
        glow = QColor(lamp)
        glow.setAlpha(50 + int(50 * abs(math.sin(t / 4))))
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, h - 9), 6.5, 6.5)
        lamp.setAlpha(160 + int(90 * abs(math.sin(t / 4))))
        p.setBrush(lamp)
        p.drawEllipse(QPointF(cx, h - 9), 3.1, 3.1)

    def _draw_z(self, p, x, y, w, h, stroke, pad=0.0):
        """Single-stroke Z: top bar → diagonal → bottom bar, thick pen with
        RoundJoin so the strokes connect smoothly."""
        bar = h * 0.26 + pad
        path = QPainterPath()
        path.moveTo(x, y + bar / 2)
        path.lineTo(x + w, y + bar / 2)
        path.lineTo(x, y + h - bar / 2)
        path.lineTo(x + w, y + h - bar / 2)
        pen = QPen()
        pen.setWidthF(bar)
        pen.setCapStyle(Qt.FlatCap)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setBrush(stroke)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


# ========== Windows helpers: focus ZCode, detect its process ==========
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    _u32 = ctypes.windll.user32
    _k32 = ctypes.windll.kernel32
    _ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
else:
    _u32 = _k32 = None
    _ENUMPROC = None


def _zcode_windows():
    """Visible top-level ZCode.exe windows → [(hwnd, title), ...]."""
    if _u32 is None:
        return []
    out = []

    def _cb(h, l):
        try:
            if not _u32.IsWindowVisible(h):
                return True
            pid = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            k = _k32.OpenProcess(0x1000, False, pid.value)
            if not k:
                return True
            try:
                buf = ctypes.create_unicode_buffer(512)
                size = wintypes.DWORD(512)
                if _k32.QueryFullProcessImageNameW(k, 0, buf, ctypes.byref(size)):
                    if "zcode" in os.path.basename(buf.value).lower():
                        n = _u32.GetWindowTextLengthW(h)
                        tb = ctypes.create_unicode_buffer(n + 1)
                        _u32.GetWindowTextW(h, tb, n + 1)
                        out.append((h, tb.value))
            finally:
                _k32.CloseHandle(k)
        except Exception:
            pass
        return True

    cb = _ENUMPROC(_cb)
    _u32.EnumWindows(cb, 0)
    return out


def activate_zcode():
    """Bring the ZCode window to the front (restore if minimized)."""
    wins = _zcode_windows()
    if not wins:
        return False
    hwnd = wins[0][0]
    try:
        if _u32.IsIconic(hwnd):
            _u32.ShowWindow(hwnd, 9)   # SW_RESTORE
        else:
            _u32.ShowWindow(hwnd, 5)   # SW_SHOW
        fg = _u32.GetForegroundWindow()
        cur = _k32.GetCurrentThreadId()
        fgt = _u32.GetWindowThreadProcessId(fg, None) if fg else 0
        if fgt and fgt != cur:
            _u32.AttachThreadInput(cur, fgt, True)
        _u32.SetForegroundWindow(hwnd)
        _u32.SetFocus(hwnd)
        if fgt and fgt != cur:
            _u32.AttachThreadInput(cur, fgt, False)
        return True
    except Exception:
        return False


def _zcode_process_running():
    """Whether a ZCode.exe process is alive (tool-help snapshot, <5ms)."""
    if _k32 is None:
        return True
    TH32CS_SNAPPROCESS = 0x2

    class _PE32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_wchar * 260)]

    snap = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return True
    found = False
    try:
        e = _PE32()
        e.dwSize = ctypes.sizeof(e)
        if _k32.Process32FirstW(snap, ctypes.byref(e)):
            while True:
                if "zcode" in str(e.szExeFile).lower():
                    found = True
                    break
                if not _k32.Process32NextW(snap, ctypes.byref(e)):
                    break
    finally:
        _k32.CloseHandle(snap)
    return found


class ZCodeProcessMonitor(QObject):
    """Lifecycle monitor: when the ZCode process closes, the pet exits too.
    Only triggers after the ZCode process has been seen at least once;
    2 consecutive misses before exiting (ZCode restarts won't kill it)."""

    def __init__(self, log_fn, on_closed, interval_ms=4000):
        super().__init__()
        self._log = log_fn
        self._on_closed = on_closed
        self.seen = False
        self.miss = 0
        self.timer = QTimer(self, interval=interval_ms, timeout=self._check)
        self.timer.start()

    def _check(self):
        running = _zcode_process_running()
        if running:
            if not self.seen:
                self._log("lifecycle", T("log_lc_seen"))
            self.seen = True
            self.miss = 0
        elif self.seen:
            self.miss += 1
            if self.miss >= 2:
                self._log("lifecycle", T("log_lc_exit"))
                self.timer.stop()
                self._on_closed()


# ========== Bubble: foldable stack of task cards ==========
class BubbleWindow(QWidget):
    """Task cards floating above the pet (ChatGPT-pet style):
    folded = the most relevant card plus un-viewed done cards;
    expanded = one card per session. Click a card → jump to ZCode;
    click empty space → fold/unfold; follows the pet; stays pinned while
    anything needs attention, hides when all cards are viewed."""

    CARD_H = 62
    GAP = 6
    MAX_CARDS = 6

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("ZCode Pet")
        self.setStyleSheet(STYLE)
        self.setFixedWidth(BUBBLE_W)
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(self.GAP)
        self.card_sids = []
        self.on_card = None
        self.on_background = None

    @staticmethod
    def _ellipsize(t, n):
        t = " ".join(str(t).split())
        return t[:n] + "…" if len(t) > n else t

    def rebuild(self, cards, sids):
        self.card_sids = list(sids)
        while self.outer.count():
            item = self.outer.takeAt(0)
            w = item.widget()
            if w:
                w.hide()          # 立即从绘制中移除，避免半透明残影
                w.deleteLater()
        for (title, status, icon, color) in cards:
            f = QFrame()
            f.setObjectName("bubble")
            f.setFixedHeight(self.CARD_H)
            f.setCursor(Qt.PointingHandCursor)
            v = QVBoxLayout(f)
            v.setContentsMargins(12, 9, 12, 9)
            v.setSpacing(2)
            row = QHBoxLayout()
            row.setSpacing(6)
            t = QLabel(title)
            t.setObjectName("bubbleTitle")
            s = QLabel(status)
            s.setObjectName("bubbleStatus")
            ic = QLabel(icon)
            ic.setObjectName("bubbleIcon")
            ic.setStyleSheet("color:{0};".format(color))
            row.addWidget(t, 1)
            row.addWidget(ic, 0, Qt.AlignVCenter)
            v.addLayout(row)
            v.addWidget(s)
            self.outer.addWidget(f)
        if cards:
            self.setFixedHeight(len(cards) * self.CARD_H
                                + (len(cards) - 1) * self.GAP)
        else:
            self.setFixedHeight(10)

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        y = e.pos().y()
        idx = int(y // (self.CARD_H + self.GAP)) if y >= 0 else -1
        in_card = (0 <= idx < len(self.card_sids)
                   and (y - idx * (self.CARD_H + self.GAP)) <= self.CARD_H)
        if in_card and self.on_card:
            self.on_card(self.card_sids[idx])
        elif self.on_background:
            self.on_background()

    def popup(self, pet):
        self.reposition(pet)
        self.show()
        self.raise_()

    def reposition(self, pet):
        """Above the pet, centered; flips below when the top has no room;
        clamped to the pet's screen (taskbar excluded)."""
        center = pet.geometry().center()
        scr = QGuiApplication.screenAt(center)
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        geo = scr.availableGeometry()
        x = pet.x() + (pet.width() - BUBBLE_W) // 2
        x = min(max(x, geo.left() + 2), geo.right() - BUBBLE_W - 2)
        y = pet.y() - self.height() - 6
        if y < geo.top() + 2:
            below = pet.y() + pet.height() + 6
            if below + self.height() <= geo.bottom() - 2:
                y = below
        y = max(y, geo.top() + 2)
        self.move(x, y)


# ========== Main window: pet + card stack ==========
STYLE = """
QLabel {{ color:{FG}; font-family:'Segoe UI'; font-size:13px; background:transparent; }}
QLabel#chip {{ font-family:'Segoe UI','Microsoft YaHei'; font-size:11px;
               font-weight:bold; color:{FG_SUBTLE}; }}
QFrame#bubble {{ background:rgba(22,22,22,235); border:1px solid {BORDER};
                 border-radius:10px; }}
QLabel#bubbleTitle {{ color:{FG}; font-size:13px; font-weight:bold;
                      background:transparent; }}
QLabel#bubbleStatus {{ font-family:'Segoe UI','Microsoft YaHei'; font-size:12px;
                       font-weight:bold; background:transparent; }}
QLabel#bubbleIcon {{ font-size:13px; font-weight:bold; background:transparent; }}
QMenu {{ background:{BG}; color:{FG}; border:1px solid {BORDER}; font-size:13px; }}
QMenu::item:selected {{ background:{BRAND}; color:{BG}; }}
QMenu::separator {{ height:1px; background:{BORDER}; margin:3px 0; }}
""".format(BG=BG, BORDER=BORDER, FG=FG, BRAND=BRAND, FG_SUBTLE=FG_SUBTLE)

PHASE_COLOR = {"idle": FG_SUBTLE, "thinking": WARNING, "executing": BRAND,
               "waiting": WAITING, "done": SUCCESS}


def _new_session():
    return {"state": "idle", "steps": 0, "tool": None, "t0": None, "task": "",
            "is_remote": False, "done_at": 0.0, "last_ts": 0.0, "result": "",
            "acked": False}


class PetWindow(QWidget):
    def __init__(self, server=True, tray=True, hotkey=True, port=DEFAULT_PORT):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("ZCode Pet")
        self.setStyleSheet(STYLE)

        self.sess = {}
        self.sessions = set()
        self._seen_events = set()
        self._drag_from = None
        self._win_from = None
        self._moved = False
        self._face_mood = "idle"
        self._bubble_text = ""
        self._bubble_until = 0.0
        self.bubble_expanded = False
        self._bubble_sig = None
        self._bubble_manual = False

        self._build_ui()
        self._clock = QTimer(self, timeout=self._tick_clock, interval=1000)
        self._clock.start()
        self._save_pos_timer = QTimer(self, singleShot=True, interval=400,
                                      timeout=self._save_pos)
        self._bubble_timer = QTimer(self, singleShot=True, interval=20000,
                                    timeout=self._auto_hide_bubble)

        self.srv = None
        self.port = None
        if server:
            self.srv = EventServer(port)
            if self.srv.start():
                BRIDGE.event_received.connect(self.handle_event)
                self.port = self.srv.port

        self.tunnels = None
        if server and os.path.exists(TUNNELS_PATH):
            self.tunnels = TunnelManager(self._log)

        self.lifecycle = None
        if server and sys.platform == "win32":
            self.lifecycle = ZCodeProcessMonitor(self._log, self._quit)

        self.tray = None
        if tray and QSystemTrayIcon.isSystemTrayAvailable():
            self._init_tray()

        self.hotkey_ok = False
        self._hk_filter = None
        if hotkey and sys.platform == "win32":
            try:
                import ctypes
                if ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID,
                                                       MOD_CONTROL | MOD_ALT, VK_Z):
                    self._hk_filter = HotkeyFilter(self.toggle_visible)
                    QApplication.instance().installNativeEventFilter(self._hk_filter)
                    self.hotkey_ok = True
            except Exception:
                pass

        self.setFixedSize(*PET_SIZE)
        self._restore_pos()

    # ---------- UI ----------
    def _build_ui(self):
        self.root = QWidget(self)
        v = QVBoxLayout(self.root)
        v.setContentsMargins(2, 0, 2, 2)
        v.setSpacing(2)
        self.v = v

        self.bwin = BubbleWindow()
        self.bwin.on_card = self._on_card_click
        self.bwin.on_background = self._toggle_fold

        self.face = ZLogoFace()
        self.face.setFixedWidth(136)
        v.addWidget(self.face, 0, Qt.AlignHCenter)

        self.chip = QLabel(T("chip_idle"))
        self.chip.setObjectName("chip")
        self.chip.setAlignment(Qt.AlignCenter)
        self.chip.setFixedHeight(26)
        chip_shadow = QGraphicsDropShadowEffect(self)
        chip_shadow.setBlurRadius(8)
        chip_shadow.setOffset(0, 1)
        chip_shadow.setColor(QColor(0, 0, 0, 200))
        self.chip.setGraphicsEffect(chip_shadow)
        v.addWidget(self.chip)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.root)

    def _paint_tray_icon(self):
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        pt = QPainter(pm)
        pt.setRenderHint(QPainter.Antialiasing)
        s = 2.4
        path = QPainterPath()
        path.moveTo(3 * s, 5.2 * s)
        path.lineTo(13 * s, 5.2 * s)
        path.lineTo(3 * s, 10.8 * s)
        path.lineTo(13 * s, 10.8 * s)
        pen = QPen(QColor(BRAND))
        pen.setWidthF(2.6 * s)
        pen.setCapStyle(Qt.FlatCap)
        pen.setJoinStyle(Qt.RoundJoin)
        pt.setPen(pen)
        pt.setBrush(Qt.NoBrush)
        pt.drawPath(path)
        pt.end()
        return QIcon(pm)

    def _build_tray_menu(self):
        menu = QMenu()
        menu.addAction(T("menu_showhide"), self.toggle_visible)
        menu.addAction(T("menu_fold"), self._toggle_bubble)
        menu.addAction(T("menu_demo"), self.run_demo)
        menu.addSeparator()
        menu.addAction(T("menu_reset"), self._reset_pos)
        menu.addSeparator()
        menu.addAction(T("menu_quit"), self._quit)
        return menu

    def _init_tray(self):
        icon = self._paint_tray_icon()
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(T("tray_tip"))
        self.tray.setContextMenu(self._build_tray_menu())
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

    def _on_tray(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_visible()

    # ---------- position ----------
    def _default_pos(self):
        geo = QGuiApplication.primaryScreen().availableGeometry()
        return QPoint(geo.right() - self.width() - 44, geo.bottom() - self.height() - 56)

    def _restore_pos(self):
        st = QSettings("ZCode", "ZCodePet")
        pos = st.value("pos", type="QPoint") if st.contains("pos") else None
        if pos is not None and self._on_screen(pos):
            self.move(pos)
        else:
            self.move(self._default_pos())
        self._clamp_pos()

    def _on_screen(self, pos):
        for scr in QGuiApplication.screens():
            if scr.availableGeometry().adjusted(-40, -40, 40, 40).contains(pos):
                return True
        return False

    def _clamp_pos(self):
        center = self.geometry().center()
        scr = QGuiApplication.screenAt(center)
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        geo = scr.availableGeometry()
        p = self.pos()
        x = min(max(p.x(), geo.left()), geo.right() - self.width() + 1)
        y = min(max(p.y(), geo.top()), geo.bottom() - self.height() + 1)
        if (x, y) != (p.x(), p.y()):
            self.move(QPoint(x, y))

    def _reset_pos(self):
        self.move(self._default_pos())

    def _save_pos(self):
        QSettings("ZCode", "ZCodePet").setValue("pos", self.pos())

    def moveEvent(self, e):
        if self.bwin.isVisible():
            self.bwin.reposition(self)
        e.accept()

    # ---------- bubble ----------
    def _show_bubble(self):
        self._refresh_bubble()
        self.bwin.popup(self)
        self._bubble_timer.start()

    def _hide_bubble(self):
        self._bubble_manual = True
        self.bwin.hide()

    def _auto_hide_bubble(self):
        if self._card_entries():
            return   # active tasks / un-viewed done cards stay pinned
        self.bwin.hide()

    def _toggle_bubble(self):
        if not self.bwin.isVisible():
            self.bubble_expanded = False
            self._show_bubble()
        else:
            self.bubble_expanded = not self.bubble_expanded
            self._refresh_bubble()
            self.bwin.reposition(self)
            self._bubble_timer.start()

    def _toggle_fold(self):
        self.bubble_expanded = not self.bubble_expanded
        self._refresh_bubble()
        self.bwin.reposition(self)
        self._bubble_timer.start()

    def _jump_zcode(self):
        ok = activate_zcode()
        self._log("info", T("log_jump", ok=T("ok_log") if ok else T("ok_miss")))

    def _on_card_click(self, sid):
        s = self.sess.get(sid, {})
        ok = activate_zcode()
        if s and s["task"] and s["state"] in ("done", "idle") and not s.get("acked"):
            s["acked"] = True
            self._log("info", T("log_card_ack", sid=sid[:8]))
        self._log("info", T("log_jump_card", sid=sid[:8],
                            r=T("remote_log") if s.get("is_remote") else "",
                            ok=T("ok_log") if ok else T("ok_miss")))
        if not self._card_entries():
            self.bwin.hide()
        else:
            self._refresh_bubble()
            self.bwin.reposition(self)

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
            self.bwin.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    # ---------- mouse / menu ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_from = e.globalPos()
            self._win_from = self.pos()
            self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag_from and (e.buttons() & Qt.LeftButton):
            d = e.globalPos() - self._drag_from
            if d.manhattanLength() > 6:
                self._moved = True
            if self._moved:
                self.move(self._win_from + d)
                self._clamp_pos()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag_from is not None:
            if not self._moved and self.face.geometry().contains(e.pos()):
                self._jump_zcode()
            self._drag_from = None
            self._clamp_pos()
            self._save_pos_timer.start(50)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction(T("menu_fold") if self.bwin.isVisible() else T("menu_cards"),
                    self._toggle_bubble)
        if self.bwin.isVisible():
            m.addAction(T("menu_hide"), self._hide_bubble)
        m.addAction(T("menu_demo"), self.run_demo)
        m.addAction(T("menu_reset"), self._reset_pos)
        lang_menu = m.addMenu(T("menu_lang"))
        for code in ("zh", "en"):
            a = lang_menu.addAction(I18N[code]["lang_name"])
            a.setCheckable(True)
            a.setChecked(CURRENT_LANG == code)
            a.triggered.connect(lambda _, c=code: self._set_lang(c))
        m.addSeparator()
        m.addAction(T("menu_quit"), self._quit)
        m.exec_(e.globalPos())

    def _set_lang(self, code):
        if code == CURRENT_LANG:
            return
        set_lang(code)
        self._log("info", T("log_lang"))
        self._bubble_sig = None
        self._refresh_chip()
        if self.bwin.isVisible():
            self._refresh_bubble()
            self.bwin.reposition(self)
        if self.tray:
            self.tray.setToolTip(T("tray_tip"))
            self.tray.setContextMenu(self._build_tray_menu())

    # ---------- events ----------
    @staticmethod
    def _normalize(ev):
        name = (ev.get("hook_event_name") or ev.get("hookEventName")
                or ev.get("event") or "Unknown")
        sid = str(ev.get("session_id") or ev.get("sessionId") or "-")
        tool = ev.get("tool_name") or ev.get("toolName") or "?"
        ti = ev.get("tool_input") or ev.get("toolInput") or {}
        cwd = ev.get("cwd") or ""
        prompt = ev.get("prompt") or ev.get("user_prompt") or ev.get("message") or ""
        err = ev.get("error") or ev.get("message") or ""
        result = (ev.get("responsePreview") or ev.get("responseText")
                  or ev.get("last_assistant_message") or "")
        return {"name": str(name), "sid": sid, "tool": str(tool), "tool_input": ti,
                "cwd": str(cwd), "prompt": str(prompt), "error": err,
                "result": str(result)}

    def _sess(self, sid, cwd=""):
        if sid not in self.sess:
            self.sess[sid] = _new_session()
        s = self.sess[sid]
        if cwd and not s["is_remote"]:
            s["is_remote"] = cwd.startswith("/")
        return s

    def handle_event(self, ev):
        if not isinstance(ev, dict):
            ev = {"raw": str(ev)}
        if not ev:
            self._log("warn", T("log_empty"))
            return
        e = self._normalize(ev)
        sid = e["sid"]
        if sid not in self.sessions:
            self.sessions.add(sid)
            s0 = self._sess(sid, e["cwd"])
            self._log("session", T("log_join", sid=sid[:8],
                                   where=T("where_log") if s0["is_remote"] else T("local_log"),
                                   n=len(self.sessions)))
        if e["name"] not in self._seen_events:
            self._seen_events.add(e["name"])
            try:
                self._log("raw", json.dumps(ev, ensure_ascii=False))
            except Exception:
                pass
        s = self._sess(sid, e["cwd"])
        s["last_ts"] = time.time()
        handler = getattr(self, "_ev_" + e["name"], None)
        if handler:
            try:
                handler(e, s)
            except Exception as ex:
                self._log("fail", T("log_err", n=e["name"], e=ex))
        else:
            self._log("info", T("log_unknown", n=e["name"]))
        self._apply_aggregate()
        self._refresh_chip()
        self._refresh_bubble()

    @staticmethod
    def _tool_snippet(ti):
        if isinstance(ti, dict):
            for k in ("command", "file_path", "path", "pattern", "url", "query", "prompt"):
                if ti.get(k):
                    sv = str(ti[k]).replace("\n", " ")
                    return sv[:72] + ("…" if len(sv) > 72 else "")
        return ""

    def _ev_SessionStart(self, e, s):
        self._log("session", T("log_ready", src=e.get("source") or e.get("reason") or "startup"))
        self._bubble(T("bubble_ready"))

    def _ev_UserPromptSubmit(self, e, s):
        s["state"] = "thinking"
        s["steps"] = 0
        s["tool"] = None
        s["task"] = e["prompt"]
        s["t0"] = time.time()
        s["done_at"] = 0.0
        s["result"] = ""
        s["acked"] = False
        self._bubble_manual = False
        self._show_bubble()
        self._log("task", T("log_new_task", w=T("where_log") if s["is_remote"] else "",
                            t=e["prompt"][:80] + ("…" if len(e["prompt"]) > 80 else "")))

    def _ev_PreToolUse(self, e, s):
        s["tool"] = e["tool"]
        s["state"] = "executing"
        if s["t0"] is None:
            s["t0"] = time.time()
        snip = self._tool_snippet(e["tool_input"])
        self._log("run", "{0}▶ {1} {2}".format(
            T("where_log") if s["is_remote"] else "", e["tool"], snip))

    def _ev_PostToolUse(self, e, s):
        s["steps"] += 1
        if e["tool"] != "?":
            s["tool"] = e["tool"]
        self._log("ok", T("log_tool_done", w=T("where_log") if s["is_remote"] else "",
                          tool=s["tool"] or "?", n=s["steps"]))

    def _ev_PostToolUseFailure(self, e, s):
        err = e["error"]
        if isinstance(err, dict):
            err = json.dumps(err, ensure_ascii=False)
        self._log("fail", T("log_tool_fail", tool=e["tool"], e=str(err)[:160]))
        self.face.shake()

    def _ev_PermissionRequest(self, e, s):
        s["tool"] = e["tool"]
        s["state"] = "waiting"
        snip = self._tool_snippet(e["tool_input"])
        self._log("warn", T("log_perm", tool=e["tool"], snip=snip))
        self._bubble(T("bubble_wait"))
        self._show_bubble()
        if self.tray:
            self.tray.showMessage("ZCode Pet",
                                  "{0}{1}".format(T("where_log") if s["is_remote"] else "",
                                                  e["tool"]),
                                  QSystemTrayIcon.Warning, 6000)

    def _ev_Stop(self, e, s):
        s["state"] = "done"
        s["done_at"] = time.time()
        if not s["task"]:
            s["task"] = T("untitled_log")
        if e["result"]:
            s["result"] = e["result"]
        self._log("ok", T("log_stop", w=T("where_log") if s["is_remote"] else "",
                          n=s["steps"], t=self._elapsed_str(s)))
        self._bubble(T("bubble_done"))
        self._show_bubble()
        if self.tray:
            self.tray.showMessage("ZCode Pet",
                                  T("log_stop", w=T("where_log") if s["is_remote"] else "",
                                    n=s["steps"], t=self._elapsed_str(s)),
                                  QSystemTrayIcon.Information, 5000)

    # ---------- aggregation ----------
    def _pick_active(self):
        now = time.time()
        best = None
        for sid, s in self.sess.items():
            if s["state"] == "done" and s["done_at"] and now - s["done_at"] > DONE_WINDOW:
                s["state"] = "idle"
            key = (PRIORITY[s["state"]], -s["last_ts"])
            if best is None or key < best[0]:
                best = (key, sid, s)
        if best is None:
            return "-", _new_session()
        return best[1], best[2]

    def _apply_aggregate(self):
        _, s = self._pick_active()
        if s["state"] != self._face_mood:
            self._face_mood = s["state"]
            self.face.set_mood(self._face_mood)

    def _active_count(self):
        return sum(1 for s in self.sess.values() if s["state"] != "idle")

    # ---------- UI refresh ----------
    def _bubble(self, text):
        self._bubble_text = text
        self._bubble_until = time.time() + 6

    def _elapsed_str(self, s):
        if not s["t0"] or s["state"] == "idle":
            return "00:00"
        sec = int(time.time() - s["t0"])
        return "{0:02d}:{1:02d}".format(sec // 60, sec % 60)

    def _state_line(self, s, compact=False):
        t = self._elapsed_str(s)
        remote = s["is_remote"]
        if compact:
            tag = "☁" if remote else ""
            tool = s["tool"] or ""
            if len(tool) > 8:
                tool = tool[:8] + "…"
            if s["state"] == "thinking":
                return tag + T("chip_thinking"), WARNING
            if s["state"] == "executing":
                return tag + T("chip_exec", tool=tool, steps=s["steps"], t=t), BRAND
            if s["state"] == "waiting":
                return tag + T("chip_wait", tool=tool), WAITING
            if s["state"] == "done":
                return tag + T("chip_done", steps=s["steps"], t=t), SUCCESS
            return T("chip_idle"), FG_SUBTLE
        w = T("where_remote") if remote else ""
        if s["state"] == "thinking":
            return T("full_thinking", w=w), WARNING
        if s["state"] == "executing":
            return T("full_exec", w=w, tool=s["tool"] or "…", steps=s["steps"], t=t), BRAND
        if s["state"] == "waiting":
            return T("full_wait", w=w, tool=s["tool"] or ""), WAITING
        if s["state"] == "done":
            return T("full_done", w=w, steps=s["steps"], t=t), SUCCESS
        return T("full_idle"), FG_SUBTLE

    def _tick_clock(self):
        _, s = self._pick_active()
        if s["state"] in ("thinking", "executing", "waiting"):
            self._refresh_chip()
        if self._card_entries() and not self.bwin.isVisible() and not self._bubble_manual:
            self._show_bubble()
        if self.bwin.isVisible():
            self._refresh_bubble()
        self._apply_aggregate()

    def _refresh_chip(self):
        _, s = self._pick_active()
        txt, c = self._state_line(s, compact=True)
        n = self._active_count()
        if n > 1 and not self._bubble_text:
            txt = txt + "+{0}".format(n - 1)
        if self._bubble_text and time.time() < self._bubble_until:
            txt, c = self._bubble_text, PHASE_COLOR.get(s["state"], FG_SUBTLE)
        elif self._bubble_text:
            self._bubble_text = ""
        self.chip.setText(html.escape(txt))
        self.chip.setStyleSheet("color:{0};font-family:'Segoe UI','Microsoft YaHei';"
                                "font-size:11px;font-weight:bold;".format(c))

    def _card_texts(self, s):
        where = T("where_remote") if s["is_remote"] else ""
        icon, color = {"waiting": ("⏸", WAITING), "executing": ("▸", BRAND),
                       "thinking": ("◐", WARNING), "done": ("✓", SUCCESS),
                       "idle": ("✓", SUCCESS)}.get(s["state"], ("●", FG_SUBTLE))
        title = s["task"] if s["task"] else T("card_untitled")
        if s["state"] in ("done", "idle") and s["task"]:
            if s["result"]:
                status = T("card_done_res", w=where, result=s["result"])
            else:
                status = T("card_done", w=where, steps=s["steps"],
                           t=self._elapsed_str(s))
        elif s["state"] == "executing":
            status = T("card_exec", w=where, tool=s["tool"] or "")
        elif s["state"] == "waiting":
            status = T("card_wait", w=where, tool=s["tool"] or "")
        elif s["state"] == "thinking":
            status = T("card_think", w=where)
        else:
            status = T("card_idle", w=where)
        return (BubbleWindow._ellipsize(title, 24),
                BubbleWindow._ellipsize(status, 34), icon, color)

    def _card_entries(self):
        out = [(sid, s) for sid, s in self.sess.items()
               if s["state"] in ("thinking", "executing", "waiting")
               or (s["state"] == "done" and not s.get("acked"))
               or (s["task"] and not s.get("acked"))]
        out.sort(key=lambda kv: -kv[1]["last_ts"])
        return out[:BubbleWindow.MAX_CARDS]

    def _refresh_bubble(self):
        entries = self._card_entries()
        if not self.bubble_expanded:
            active_sid, _ = self._pick_active()
            agg = [(sid, s) for sid, s in entries if sid == active_sid]
            pending = [(sid, s) for sid, s in entries
                       if sid != active_sid
                       and s["state"] in ("done", "idle")]
            entries = (agg + pending)[:BubbleWindow.MAX_CARDS] or entries[:1]
        cards = [self._card_texts(s) for _, s in entries]
        sids = [sid for sid, _ in entries]
        sig = repr((cards, sids))
        if sig != self._bubble_sig:
            self._bubble_sig = sig
            self.bwin.rebuild(cards, sids)

    def _log(self, kind, msg):
        try:
            if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
                open(LOG_PATH, "w", encoding="utf-8").close()
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write("{0} [{1}] {2}\n".format(time.strftime("%H:%M:%S"), kind, msg))
        except Exception:
            pass

    # ---------- demo ----------
    def run_demo(self):
        self._hide_bubble()
        seq = []
        for delay, payload in DEMO_SEQUENCE:
            p = dict(payload)
            if p.get("hook_event_name") == "UserPromptSubmit":
                p["prompt"] = T("demo_prompt")
            seq.append((delay, p))
        t0 = 400
        for delay, payload in seq:
            QTimer.singleShot(t0 + delay, lambda p=payload: self.handle_event(dict(p)))

    # ---------- quit ----------
    def _quit(self):
        self._save_pos()
        if self.srv:
            self.srv.stop()
        if self.tunnels:
            self.tunnels.stop_all()
        self.bwin.close()
        if self._hk_filter:
            try:
                import ctypes
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
            except Exception:
                pass
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    def closeEvent(self, e):
        self._save_pos()
        e.accept()


# ========== demo script ==========
DEMO_SEQUENCE = [
    (0,     {"hook_event_name": "SessionStart", "session_id": "demo-0001", "source": "startup"}),
    (900,   {"hook_event_name": "UserPromptSubmit", "session_id": "demo-0001"}),
    (2100,  {"hook_event_name": "PreToolUse", "session_id": "demo-0001",
             "tool_name": "Read", "tool_input": {"file_path": "src/pages/Login.tsx"}}),
    (2800,  {"hook_event_name": "PostToolUse", "session_id": "demo-0001", "tool_name": "Read"}),
    (3600,  {"hook_event_name": "PreToolUse", "session_id": "demo-0001",
             "tool_name": "Edit", "tool_input": {"file_path": "src/pages/Login.tsx"}}),
    (4400,  {"hook_event_name": "PostToolUse", "session_id": "demo-0001", "tool_name": "Edit"}),
    (5300,  {"hook_event_name": "PreToolUse", "session_id": "demo-0001",
             "tool_name": "Bash", "tool_input": {"command": "npm test -- login"}}),
    (6100,  {"hook_event_name": "PostToolUseFailure", "session_id": "demo-0001",
             "tool_name": "Bash", "error": "1 test failed: wrap.spec.tsx:42"}),
    (7200,  {"hook_event_name": "PreToolUse", "session_id": "demo-0001",
             "tool_name": "Edit", "tool_input": {"file_path": "src/pages/Login.tsx"}}),
    (8000,  {"hook_event_name": "PostToolUse", "session_id": "demo-0001", "tool_name": "Edit"}),
    (8900,  {"hook_event_name": "PermissionRequest", "session_id": "demo-0001",
             "tool_name": "Bash", "tool_input": {"command": "git commit -m 'fix: login wrap'"}}),
    (12400, {"hook_event_name": "PreToolUse", "session_id": "demo-0001",
             "tool_name": "Bash", "tool_input": {"command": "git commit -m 'fix: login wrap'"}}),
    (13200, {"hook_event_name": "PostToolUse", "session_id": "demo-0001", "tool_name": "Bash"}),
    (14100, {"hook_event_name": "PreToolUse", "session_id": "demo-0001",
             "tool_name": "Bash", "tool_input": {"command": "npm test -- login"}}),
    (15100, {"hook_event_name": "PostToolUse", "session_id": "demo-0001", "tool_name": "Bash"}),
    (16200, {"hook_event_name": "Stop", "session_id": "demo-0001"}),
]


# ========== selftest: render every state offscreen ==========
def run_selftest():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv)
    try:
        from PyQt5.QtGui import QFontDatabase
        for f in ("consola.ttf", "segoeui.ttf", "segoeuib.ttf", "arial.ttf",
                  "arialbd.ttf", "seguisym.ttf", "segoeui-symbol.ttf", "msyh.ttc", "msyhbd.ttc", "simhei.ttf"):
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + f)
    except Exception:
        pass
    pet = PetWindow(server=False, tray=False, hotkey=False)
    pet.show()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest")
    os.makedirs(out, exist_ok=True)

    def snap(name):
        for _ in range(3):
            app.processEvents()
        pet.face.tick = int(pet.face.tick) + 17
        pet.grab().save(os.path.join(out, name + ".png"))

    def snap_bubble(name):
        for _ in range(3):
            app.processEvents()
        pet.bwin.grab().save(os.path.join(out, name + ".png"))

    snap("1-idle")
    pet.handle_event({"hook_event_name": "UserPromptSubmit", "session_id": "st",
                      "prompt": "修复登录页 1080p 按钮折行问题并补充回归测试"})
    snap("2-thinking")
    pet.handle_event({"hook_event_name": "PreToolUse", "session_id": "st",
                      "tool_name": "Read", "tool_input": {"file_path": "src/pages/Login.tsx"}})
    pet.handle_event({"hook_event_name": "PostToolUse", "session_id": "st", "tool_name": "Read"})
    pet.handle_event({"hook_event_name": "PreToolUse", "session_id": "st",
                      "tool_name": "Edit", "tool_input": {"file_path": "src/pages/Login.tsx"}})
    snap("3-executing")
    pet.handle_event({"hook_event_name": "PermissionRequest", "session_id": "st",
                      "tool_name": "Bash", "tool_input": {"command": "git commit -m fix"}})
    pet._show_bubble()
    snap_bubble("4-waiting-bubble")
    pet._hide_bubble()
    snap("4-waiting")
    pet.handle_event({"hook_event_name": "PostToolUse", "session_id": "st", "tool_name": "Edit"})
    pet.handle_event({"hook_event_name": "Stop", "session_id": "st"})
    snap("5-done")
    pet.handle_event({"hook_event_name": "UserPromptSubmit", "session_id": "remote-1",
                      "sessionId": "remote-1", "cwd": "/home/yourname/proj",
                      "prompt": "远程任务：重构 utils/date.py 并跑通单元测试"})
    pet.handle_event({"hook_event_name": "PreToolUse", "sessionId": "remote-1",
                      "toolName": "Bash", "tool_input": {"command": "pytest -q"}})
    pet._show_bubble()
    snap_bubble("6-bubble")
    pet.bubble_expanded = True
    pet._refresh_bubble()
    snap_bubble("7-expanded")
    pet._on_card_click("st")
    snap_bubble("8-after-ack")
    pet.handle_event({"hook_event_name": "Stop", "sessionId": "remote-1"})
    pet._on_card_click("remote-1")
    assert not pet.bwin.isVisible(), "bubble should hide after all cards viewed"
    pet.bubble_expanded = False
    print("SELFTEST_OK " + out)
    return 0


# ========== entry ==========
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ZCode Pet — task progress desktop pet")
    ap.add_argument("--demo", action="store_true", help="run a scripted demo after launch")
    ap.add_argument("--selftest", action="store_true", help="render states to PNG and exit")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="event server port (8765)")
    args = ap.parse_args()

    global CURRENT_LANG
    CURRENT_LANG = _detect_lang()

    if args.selftest:
        sys.exit(run_selftest())

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("ZCode Pet")

    lock = QLockFile(os.path.join(tempfile.gettempdir(), "zcode-pet.lock"))
    if not lock.tryLock(0):
        print("ZCode Pet is already running / 桌宠已在运行。")
        sys.exit(0)

    pet = PetWindow(server=True, tray=True, hotkey=True, port=args.port)
    pet.show()
    if args.demo:
        QTimer.singleShot(800, pet.run_demo)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
