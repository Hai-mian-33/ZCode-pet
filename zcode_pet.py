# -*- coding: utf-8 -*-
"""
ZCode Pet — 智谱 ZCode Logo 拟人化桌宠（真实任务进度版）
=========================================================
数据源：ZCode 官方 hooks（SessionStart / UserPromptSubmit / PreToolUse /
        PermissionRequest / PostToolUse / PostToolUseFailure / Stop）
        → curl POST 127.0.0.1:<PORT>/event → 桌宠实时显示任务进度
        兼容 snake_case / camelCase 两种事件字段格式。
多会话：每个会话独立跟踪状态，按优先级聚合显示（等确认 > 执行 > 思考 > 完成），
        互不覆盖；远程会话在气泡/托盘通知中标注「远程」。
隧道：  桌宠自动维持 tunnels.txt 中服务器的 SSH 反向隧道（断线自动重连）。
视觉：  ZCode 官方设计 token（#FEBC2E 品牌黄），Z 字单笔圆角描边。
运行：  python zcode_pet.py [--demo] [--selftest] [--port 8765]
依赖：  pip install PyQt5
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
                              QRectF, QSettings, QLockFile, QAbstractNativeEventFilter)
    from PyQt5.QtGui import (QPainter, QColor, QBrush, QPen, QLinearGradient, QFont,
                             QPainterPath, QIcon, QPixmap, QGuiApplication, QPolygonF)
    from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QVBoxLayout,
                                 QHBoxLayout, QMenu, QSystemTrayIcon, QFrame,
                                 QGraphicsDropShadowEffect)
except ImportError:
    print("请先安装依赖: pip install PyQt5")
    sys.exit(1)

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ========== ZCode 官方设计 Token（勿改）==========
BRAND     = "#FEBC2E"   # 品牌黄
BG        = "#161616"   # 主背景（气泡底色）
BORDER    = "#2A2A2A"
FG        = "#FFFFFF"
FG_SUBTLE = "#A1A1A1"
SUCCESS   = "#4ADE80"
DESTRUCT  = "#EF4444"
WARNING   = "#FBBF24"
WAITING   = "#F97316"   # 等待确认（橙）
RAW_DIM   = "#6B6B6B"

DEFAULT_PORT = 8765
PET_SIZE     = (140, 148)   # 小宠常驻尺寸（与其他桌宠同量级，无大片隐形留白）
BUBBLE_W     = 300          # 状态气泡宽度（独立置顶横幅小窗）
BUBBLE_H     = 72
DONE_WINDOW  = 8.0          # 完成状态停留秒数
LOG_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pet.log")
TUNNELS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tunnels.txt")

# 多会话聚合显示优先级：越小越优先
PRIORITY = {"waiting": 0, "executing": 1, "thinking": 2, "done": 3, "idle": 4}


# ========== Windows 窗口跳转：聚焦 ZCode ==========
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
    """枚举可见的 ZCode.exe 顶层窗口 → [(hwnd, title), ...]（最前优先）。"""
    if _u32 is None:
        return []
    out = []

    def _cb(h, l):
        try:
            if not _u32.IsWindowVisible(h):
                return True
            pid = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            k = _k32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
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
    """把 ZCode 窗口带到前台（最小化则还原）。"""
    wins = _zcode_windows()
    if not wins:
        return False
    hwnd = wins[0][0]
    try:
        if _u32.IsIconic(hwnd):
            _u32.ShowWindow(hwnd, 9)   # SW_RESTORE
        else:
            _u32.ShowWindow(hwnd, 5)   # SW_SHOW
        # 绕过前台锁定：把当前线程临时挂到前台窗口的输入队列
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
    """检查 ZCode.exe 进程是否存活（工具链快照，<5ms）。"""
    if _k32 is None:
        return True   # 非 Windows 平台不参与生命周期管理
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
        return True   # 快照失败按存活处理，宁可多留不可误退
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
    """生命周期监视：ZCode 进程关闭后，桌宠随之退出。
    曾检测到过 ZCode 进程才会触发退出（手动单独启动的桌宠不受影响）；
    连续 2 次检测不到才退，避免 ZCode 重启瞬间误杀。"""

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
                self._log("lifecycle", "已检测到 ZCode 进程 ✓（ZCode 关闭时桌宠将随之退出）")
            self.seen = True
            self.miss = 0
        elif self.seen:
            self.miss += 1
            if self.miss >= 2:
                self._log("lifecycle", "ZCode 进程已关闭 → 桌宠随之退出")
                self.timer.stop()
                self._on_closed()


# ========== 事件服务：hooks → curl POST → Qt 主线程 ==========
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
        # 204 空响应：钩子 stdout 必须为空（严格 JSON 校验），curl 也不会打印任何东西
        try:
            self.send_response(204)
            self.end_headers()
        except Exception:
            pass
        BRIDGE.event_received.emit(payload)

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/show":
            # 让已运行的桌宠显示并置顶（双击 bat / 拉起器触发）
            BRIDGE.event_received.emit({"hook_event_name": "Show"})
            self.send_response(204)
            self.end_headers()
            return
        body = b"zcode-pet ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass  # 静默


class EventServer:
    """本地 HTTP 服务，占用第一个可用端口（默认 8765 起）。"""

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


# ========== 常驻隧道管理：桌宠自己维持到服务器的反向隧道 ==========
class TunnelManager(QObject):
    """读取 tunnels.txt（user@host[:port] 每行一条），为每台服务器维持
    `ssh -N -R 8765:127.0.0.1:8765`。进程退出/端口被抢时按周期重试，
    与用户自己的 SSH 会话共存：谁先绑到远程端口谁生效，冲突无害。"""

    def __init__(self, log_fn, interval_s=60):
        super().__init__()
        self._log = log_fn
        self.procs = {}  # target(str) -> Popen
        self.timer = QTimer(self, interval=interval_s * 1000, timeout=self.ensure)
        self.timer.start()
        self.ensure()

    @staticmethod
    def _targets():
        try:
            with open(TUNNELS_PATH, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
            return lines
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
        # 清理：已退出 / 已不在配置里的进程
        for key in list(self.procs):
            p = self.procs[key]
            if key not in targets:
                if p.poll() is None:
                    p.terminate()
                del self.procs[key]
                self._log("tunnel", "隧道已移除：{0}".format(key))
            elif p.poll() is not None:
                del self.procs[key]
                self._log("tunnel", "隧道断开，准备重试：{0}".format(key))
        # 启动缺失的隧道
        for t in targets:
            if self.procs.get(t) is None or self.procs[t].poll() is not None:
                try:
                    kwargs = {}
                    if os.name == "nt":
                        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
                    p = subprocess.Popen(self._cmd(t), stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, **kwargs)
                    self.procs[t] = p
                    self._log("tunnel", "隧道已建立 → {0}".format(t))
                except Exception as e:
                    self._log("tunnel", "隧道启动失败 {0}: {1}".format(t, e))

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


# ========== 全局热键 Ctrl+Alt+Z（Windows）==========
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL = 0x1, 0x2
VK_Z = 0x5A
HOTKEY_ID = 0x5A50  # "ZP"


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


# ========== 核心：ZCode Logo 拟人化绘制 ==========
class ZLogoFace(QWidget):
    """把 ZCode 的粗体「Z」Logo 拟人化：眼睛/嘴巴/思考气泡/状态灯。
    Z 字用一条三段折线加粗描边画出（RoundJoin 圆角连接），笔画衔接平滑。"""

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

    # ---- 动画 ----
    def _do_blink(self):
        if self.mood == "done":
            return  # done 是闭眼笑，不再眨
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

    # ---- 绘制 ----
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

        # 阴影层（略粗，兼作柔和描边）
        self._draw_z(p, z_x + 1.5, z_y + 2.5, z_w, z_h, QColor(0, 0, 0, 130), pad=2.0)

        # Z 字主体（品牌黄渐变，单笔描边）
        grad = QLinearGradient(z_x, z_y, z_x, z_y + z_h)
        grad.setColorAt(0.0, QColor("#FFD666"))
        grad.setColorAt(1.0, QColor(BRAND))
        self._draw_z(p, z_x, z_y, z_w, z_h, QBrush(grad))

        # ---- 眼睛（顶横杠中央）----
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

        # ---- 嘴巴（斜笔画正中点）----
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

        # ---- 思考：头顶冒 Z 字气泡（带轻微上浮）----
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

        # ---- 等待：头顶感叹气泡 ----
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

        # ---- 完成：四角闪光 ----
        if self.mood == "done":
            for (fx, fy) in ((z_x - 6, z_y + 4), (z_x + z_w + 6, z_y + 10),
                             (z_x + 2, z_y + z_h + 4), (z_x + z_w - 4, z_y + z_h - 2)):
                a = 120 + int(120 * abs(math.sin((t + fx) / 3)))
                c = QColor(SUCCESS)
                c.setAlpha(a)
                p.setBrush(c)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(fx, fy), 2.2, 2.2)

        # ---- 状态灯（底部）----
        lamp = QColor(self.MOOD_LAMP.get(self.mood, FG_SUBTLE))
        glow = QColor(lamp)
        glow.setAlpha(50 + int(50 * abs(math.sin(t / 4))))
        p.setBrush(glow)
        p.drawEllipse(QPointF(cx, h - 9), 6.5, 6.5)
        lamp.setAlpha(160 + int(90 * abs(math.sin(t / 4))))
        p.setBrush(lamp)
        p.drawEllipse(QPointF(cx, h - 9), 3.1, 3.1)

    def _draw_z(self, p, x, y, w, h, stroke, pad=0.0):
        """单笔 Z：顶横 → 斜笔 → 底横，粗描边 + RoundJoin 圆角连接。"""
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


# ========== 状态气泡：可折叠的多卡片任务列表 ==========
class BubbleWindow(QWidget):
    """悬浮在小宠上方的任务卡片堆（类 ChatGPT 桌宠样式）：
    折叠 = 只显示当前最重要的一张卡；展开 = 每个会话一张卡竖向堆叠。
    点卡片跳转对应会话的 ZCode 窗口；点卡片外空白切换折叠/展开；
    跟随小宠移动；20 秒无事件自动收起（任务进行中常驻）。"""

    CARD_H = 62
    GAP = 6
    MAX_CARDS = 6

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("ZCode Pet 状态")
        self.setStyleSheet(STYLE)
        self.setFixedWidth(BUBBLE_W)
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(self.GAP)
        self.card_sids = []
        self.on_card = None        # callback(sid)
        self.on_background = None  # callback()

    @staticmethod
    def _ellipsize(t, n):
        t = " ".join(str(t).split())
        return t[:n] + "…" if len(t) > n else t

    def rebuild(self, cards, sids):
        """cards: [(title, status, icon, color), ...]；sids 与之对应。"""
        self.card_sids = list(sids)
        while self.outer.count():
            item = self.outer.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
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
        """底边贴着小宠上方居中；顶部放不下且下方放得下时翻到小宠下方；
        钳在小宠所在屏幕的可用区域内（不遮任务栏）。"""
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
                y = below   # 顶部没空间 → 翻到小宠下方
        y = max(y, geo.top() + 2)
        self.move(x, y)


# ========== 主窗口：小宠 + 轻状态气泡 ==========
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

PHASE_NAME = {"idle": "Idle", "thinking": "Thinking", "executing": "Executing",
              "waiting": "Waiting", "done": "Done"}
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

        # ---- 多会话状态：sid -> session dict ----
        self.sess = {}
        self.sessions = set()
        self._seen_events = set()
        self._drag_from = None
        self._win_from = None
        self._moved = False
        self._face_mood = "idle"
        self._bubble_text = ""
        self._bubble_until = 0.0
        self.bubble_expanded = False   # 气泡：False=折叠(单卡) True=展开(多卡)
        self._bubble_sig = None
        self._bubble_manual = False    # 用户手动隐藏后，任务进行中也不自动弹出
        try:
            self.pet_scale = float(QSettings("ZCode", "ZCodePet")
                                   .value("scale", 1.0))
        except Exception:
            self.pet_scale = 1.0
        self.pet_scale = min(2.0, max(0.6, self.pet_scale))

        self._build_ui()
        self._clock = QTimer(self, timeout=self._tick_clock, interval=1000)
        self._clock.start()
        self._save_pos_timer = QTimer(self, singleShot=True, interval=400,
                                      timeout=self._save_pos)
        self._bubble_timer = QTimer(self, singleShot=True, interval=20000,
                                    timeout=self._auto_hide_bubble)

        # ---- 事件服务 ----
        self.srv = None
        self.port = None
        if server:
            self.srv = EventServer(port)
            if self.srv.start():
                BRIDGE.event_received.connect(self.handle_event)
                self.port = self.srv.port

        # ---- 常驻隧道 ----
        self.tunnels = None
        if server and os.path.exists(TUNNELS_PATH):
            self.tunnels = TunnelManager(self._log)

        # ---- 生命周期：跟随 ZCode 进程（ZCode 关闭 → 桌宠退出）----
        self.lifecycle = None
        if server and sys.platform == "win32":
            self.lifecycle = ZCodeProcessMonitor(self._log, self._quit)

        # ---- 托盘 ----
        self.tray = None
        if tray and QSystemTrayIcon.isSystemTrayAvailable():
            self._init_tray()

        # ---- 全局热键 ----
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

        self._apply_size()
        self._restore_pos()

    # ---------- UI ----------
    def _build_ui(self):
        self.root = QWidget(self)
        v = QVBoxLayout(self.root)
        v.setContentsMargins(2, 0, 2, 2)
        v.setSpacing(2)
        self.v = v

        # 状态气泡：独立置顶横幅小窗（宽 300，居中悬于小宠上方）
        self.bwin = BubbleWindow()
        self.bwin.on_card = self._on_card_click
        self.bwin.on_background = self._toggle_fold

        self.face = ZLogoFace()
        self.face.setFixedWidth(136)   # 窗口收窄后锁定 Z 的绘制宽度
        v.addWidget(self.face, 0, Qt.AlignHCenter)

        self.chip = QLabel("● idle · 待命")
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

    def _init_tray(self):
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
        icon = QIcon(pm)
        self.setWindowIcon(icon)

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("ZCode Pet — 任务进度桌宠")
        menu = QMenu()
        menu.addAction("显示 / 隐藏", self.toggle_visible)
        menu.addAction("气泡：折叠/展开", self._toggle_bubble)
        menu.addAction("演示一轮任务", self.run_demo)
        menu.addSeparator()
        menu.addAction("重置位置", self._reset_pos)
        menu.addSeparator()
        menu.addAction("退出", self._quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

    def _on_tray(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.toggle_visible()

    # ---------- 位置 ----------
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

    def _on_screen(self, pos):
        for scr in QGuiApplication.screens():
            if scr.availableGeometry().adjusted(-40, -40, 40, 40).contains(pos):
                return True
        return False

    def _clamp_pos(self):
        """边缘保护：把窗口整个钳在它所在屏幕的可用区域内（贴边停住，
        不遮任务栏；多显示器下跟随宠物中心所在的屏幕）。"""
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

    # ---------- 大小调节 ----------
    def _apply_size(self):
        """按 pet_scale 应用窗口/字体尺寸（用户右键可调，设置持久化）。"""
        sc = self.pet_scale
        self.setFixedSize(int(PET_SIZE[0] * sc), int(PET_SIZE[1] * sc))
        self.face.setFixedWidth(int(136 * sc))
        self.chip.setFixedHeight(max(18, int(26 * sc)))
        self._clamp_pos()
        if self.bwin.isVisible():
            self.bwin.reposition(self)

    def _set_scale(self, v):
        self.pet_scale = float(v)
        QSettings("ZCode", "ZCodePet").setValue("scale", self.pet_scale)
        self._apply_size()
        self._log("info", "桌宠大小 → ×{0}".format(self.pet_scale))

    def _save_pos(self):
        QSettings("ZCode", "ZCodePet").setValue("pos", self.pos())

    def moveEvent(self, e):
        if self.bwin.isVisible():
            self.bwin.reposition(self)
        e.accept()

    # ---------- 状态气泡 ----------
    def _show_bubble(self):
        self._refresh_bubble()
        self.bwin.popup(self)
        self._bubble_timer.start()   # 20 秒无事件自动收起（任务进行中会常驻）

    def _hide_bubble(self):
        self._bubble_manual = True   # 手动隐藏后，本任务期间不再自动弹出
        self.bwin.hide()

    def _auto_hide_bubble(self):
        """定时检查：只要还有进行中的任务或未查看的完成卡片就常驻；
        全部处理完（都已点击查看）才收起。"""
        if self._card_entries():
            return
        self.bwin.hide()

    def _toggle_bubble(self):
        """隐藏时弹出（折叠态）；可见时在 折叠/展开 之间切换。"""
        if not self.bwin.isVisible():
            self.bubble_expanded = False
            self._show_bubble()
        else:
            self.bubble_expanded = not self.bubble_expanded
            self._refresh_bubble()
            self.bwin.reposition(self)
            self._bubble_timer.start()

    def _toggle_fold(self):
        """点卡片外空白：折叠/展开切换。"""
        self.bubble_expanded = not self.bubble_expanded
        self._refresh_bubble()
        self.bwin.reposition(self)
        self._bubble_timer.start()

    def _jump_zcode(self):
        ok = activate_zcode()
        self._log("info", "跳转 ZCode 窗口 {0}".format("✓" if ok else "（未找到）"))

    def _on_card_click(self, sid):
        s = self.sess.get(sid, {})
        ok = activate_zcode()
        # 完成的卡片：点击 = 查看，从此不再显示（含展开态）
        if s and s["task"] and s["state"] in ("done", "idle") and not s.get("acked"):
            s["acked"] = True
            self._log("info", "卡片已查看并移除（会话 #{0}）".format(sid[:8]))
        self._log("info", "点击卡片跳转 ZCode（会话 #{0}{1}）{2}".format(
            sid[:8], "·远程" if s.get("is_remote") else "",
            "✓" if ok else "（未找到窗口）"))
        if not self._card_entries():
            self.bwin.hide()          # 所有卡片都已处理 → 收起气泡
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

    # ---------- 拖动 / 点击 / 右键 ----------
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
                self._clamp_pos()   # 拖动全程贴边保护，拖不出桌面

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._drag_from is not None:
            if not self._moved and self.face.geometry().contains(e.pos()):
                self._jump_zcode()   # 单击宠物 → 跳转 ZCode
            self._drag_from = None
            self._clamp_pos()
            self._save_pos_timer.start(50)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("气泡：折叠/展开" if self.bwin.isVisible() else "显示任务卡片",
                    self._toggle_bubble)
        if self.bwin.isVisible():
            m.addAction("隐藏任务卡片", self._hide_bubble)
        size_menu = m.addMenu("大小")
        for label, val in (("小 ×0.8", 0.8), ("标准 ×1.0", 1.0),
                           ("大 ×1.25", 1.25), ("特大 ×1.5", 1.5)):
            act = size_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(abs(self.pet_scale - val) < 0.01)
            act.triggered.connect(lambda _=False, v=val: self._set_scale(v))
        m.addAction("演示一轮任务", self.run_demo)
        m.addAction("重置位置", self._reset_pos)
        m.addSeparator()
        m.addAction("退出", self._quit)
        m.exec_(e.globalPos())

    # ---------- 事件处理 ----------
    @staticmethod
    def _normalize(ev):
        """兼容 snake_case（本机）与 camelCase（部分版本）两种事件字段。"""
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
            self._log("warn", "收到空事件（钩子未传 stdin，请检查远程钩子版本）")
            return
        e = self._normalize(ev)
        if e["name"] == "Show":
            self._ev_Show()
            return
        sid = e["sid"]
        if sid not in self.sessions:
            self.sessions.add(sid)
            s0 = self._sess(sid, e["cwd"])
            self._log("session", "会话接入 #{0}（{1}，共 {2} 个）".format(
                sid[:8], "远程" if s0["is_remote"] else "本机", len(self.sessions)))
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
                self._log("fail", "处理 {0} 出错: {1}".format(e["name"], ex))
        else:
            self._log("info", "未知事件 {0}".format(e["name"]))
        self._apply_aggregate()
        self._refresh_chip()
        self._refresh_bubble()

    def _tool_snippet(self, ti):
        if isinstance(ti, dict):
            for k in ("command", "file_path", "path", "pattern", "url", "query", "prompt"):
                if ti.get(k):
                    sv = str(ti[k]).replace("\n", " ")
                    return sv[:72] + ("…" if len(sv) > 72 else "")
        return ""

    def _ev_Show(self):
        """/show 触发：显示并置顶（双击 bat 或拉起器钩子）。"""
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()
        self._clamp_pos()
        self._log("info", "桌宠已显示并置顶")

    def _ev_SessionStart(self, e, s):
        self._log("session", "会话就绪（{0}）".format(
            e.get("source") or e.get("reason") or "startup"))
        self._bubble("就绪 (•̀ᴗ•́)و")

    def _ev_UserPromptSubmit(self, e, s):
        s["state"] = "thinking"
        s["steps"] = 0
        s["tool"] = None
        s["task"] = e["prompt"]
        s["t0"] = time.time()
        s["done_at"] = 0.0
        s["result"] = ""
        s["acked"] = False            # 新任务开始：卡片重新进入常驻列表
        self._bubble_manual = False
        self._show_bubble()
        self._log("task", "{0}新任务：{1}".format(
            "［远程］" if s["is_remote"] else "",
            e["prompt"][:80] + ("…" if len(e["prompt"]) > 80 else "")))
        self._bubble("开跑 (๑•̀ㅂ•́)و")

    def _ev_PreToolUse(self, e, s):
        s["tool"] = e["tool"]
        s["state"] = "executing"
        if s["t0"] is None:
            s["t0"] = time.time()   # 错过 UserPromptSubmit 的会话也从首步开始计时
        snip = self._tool_snippet(e["tool_input"])
        self._log("run", "{0}▶ {1} {2}".format(
            "［远程］" if s["is_remote"] else "", e["tool"], snip))

    def _ev_PostToolUse(self, e, s):
        s["steps"] += 1
        if e["tool"] != "?":
            s["tool"] = e["tool"]
        self._log("ok", "{0}✓ {1} 完成 · 第 {2} 步".format(
            "［远程］" if s["is_remote"] else "", s["tool"] or "?", s["steps"]))

    def _ev_PostToolUseFailure(self, e, s):
        err = e["error"]
        if isinstance(err, dict):
            err = json.dumps(err, ensure_ascii=False)
        self._log("fail", "✗ {0} 失败 {1}".format(e["tool"], str(err)[:160]))
        self.face.shake()

    def _ev_PermissionRequest(self, e, s):
        s["tool"] = e["tool"]
        s["state"] = "waiting"
        snip = self._tool_snippet(e["tool_input"])
        self._log("warn", "⏸ 等待权限确认：{0} {1}".format(e["tool"], snip))
        self._bubble("ZCode 在等你确认 (・_・;)")
        self._show_bubble()
        if self.tray:
            self.tray.showMessage("ZCode Pet",
                                  "⏸ {0}等待你确认：{1}".format(
                                      "远程服务器 " if s["is_remote"] else "", e["tool"]),
                                  QSystemTrayIcon.Warning, 6000)

    def _ev_Stop(self, e, s):
        s["state"] = "done"
        s["done_at"] = time.time()
        if not s["task"]:
            s["task"] = "（未命名任务）"   # 错过任务开头也要有标题，保证完成卡常驻
        if e["result"]:
            s["result"] = e["result"]
        self._log("ok", "{0}★ 任务完成 · {1} 步 · 用时 {2}".format(
            "［远程］" if s["is_remote"] else "", s["steps"], self._elapsed_str(s)))
        self._bubble("搞定！✓")
        self._show_bubble()
        if self.tray:
            self.tray.showMessage("ZCode Pet",
                                  "★ {0}任务完成 · {1} 步 · {2}".format(
                                      "远程" if s["is_remote"] else "",
                                      s["steps"], self._elapsed_str(s)),
                                  QSystemTrayIcon.Information, 5000)

    # ---------- 聚合显示 ----------
    def _pick_active(self):
        """返回 (sid, s)：所有会话里优先级最高的状态（等确认>执行>思考>完成>空闲）。
        done 超过停留时间自动回落 idle。并列时取最近有事件的。"""
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

    # ---------- UI 刷新 ----------
    def _bubble(self, text):
        """状态条短暂显示一句气泡文案，6 秒后淡回状态文本。"""
        self._bubble_text = text
        self._bubble_until = time.time() + 6

    def _elapsed_str(self, s):
        if not s["t0"] or s["state"] == "idle":
            return "00:00"
        sec = int(time.time() - s["t0"])
        return "{0:02d}:{1:02d}".format(sec // 60, sec % 60)

    def _state_line(self, s, compact=False):
        """compact=True 供小宠脚下的窄状态条使用（约 136px 宽）。"""
        t = self._elapsed_str(s)
        remote_c = s["is_remote"]
        if compact:
            tag = "☁" if remote_c else ""
            tool = s["tool"] or ""
            if len(tool) > 8:
                tool = tool[:8] + "…"
            if s["state"] == "thinking":
                return "{0}◐思考中".format(tag), WARNING
            if s["state"] == "executing":
                return "{0}▸{1}·{2}步·{3}".format(tag, tool, s["steps"], t), BRAND
            if s["state"] == "waiting":
                return "{0}⏸确认{1}".format(tag, tool), WAITING
            if s["state"] == "done":
                return "{0}✓{1}步·{2}".format(tag, s["steps"], t), SUCCESS
            return "●待命", FG_SUBTLE
        where = "远程 · " if remote_c else ""
        if s["state"] == "thinking":
            return "◐ {0}thinking · 分析任务…".format(where), WARNING
        if s["state"] == "executing":
            return "▸ {0}{1} · {2}步 · {3}".format(where, s["tool"] or "…",
                                                   s["steps"], t), BRAND
        if s["state"] == "waiting":
            return "⏸ {0}等你确认 {1}".format(where, s["tool"] or ""), WAITING
        if s["state"] == "done":
            return "✓ {0}done · {1}步 · {2}".format(where, s["steps"], t), SUCCESS
        return "● idle · 待命", FG_SUBTLE

    def _tick_clock(self):
        _, s = self._pick_active()
        if s["state"] in ("thinking", "executing", "waiting"):
            self._refresh_chip()
        # 有应显示的卡片（进行中 / 未查看的完成卡片）且未手动隐藏 → 常驻弹出
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
        # 短暂气泡文案优先于状态文本
        if self._bubble_text and time.time() < self._bubble_until:
            txt, c = self._bubble_text, PHASE_COLOR.get(s["state"], FG_SUBTLE)
        elif self._bubble_text:
            self._bubble_text = ""
        self.chip.setText(html.escape(txt))
        self.chip.setStyleSheet("color:{0};font-family:'Segoe UI','Microsoft YaHei';"
                                "font-size:{1}px;font-weight:bold;"
                                .format(c, max(9, round(11 * self.pet_scale))))

    def _card_texts(self, s):
        """单个会话 → 卡片 (title, status, icon, color)。"""
        where = "远程 · " if s["is_remote"] else ""
        icon, color = {"waiting": ("⏸", WAITING), "executing": ("▸", BRAND),
                       "thinking": ("◐", WARNING), "done": ("✓", SUCCESS),
                       "idle": ("✓", SUCCESS)}.get(s["state"], ("●", FG_SUBTLE))
        title = s["task"] if s["task"] else "（未命名任务）"
        if s["state"] in ("done", "idle") and s["task"]:
            # 已完成（含超过停留时间转为 idle 的），持续显示直到点击查看
            if s["result"]:
                status = "{0}已完成 · {1}".format(where, s["result"])
            else:
                status = "{0}已完成 · {1}步 · {2}".format(where, s["steps"],
                                                          self._elapsed_str(s))
        elif s["state"] == "executing":
            status = "{0}正在执行 · {1}".format(where, s["tool"] or "")
        elif s["state"] == "waiting":
            status = "{0}等你确认 · {1}".format(where, s["tool"] or "")
        elif s["state"] == "thinking":
            status = "{0}分析任务…".format(where)
        else:
            status = "{0}空闲".format(where)
        return (BubbleWindow._ellipsize(title, 24),
                BubbleWindow._ellipsize(status, 34), icon, color)

    def _card_entries(self):
        """当前应显示的卡片：进行中的会话 + 完成后尚未点击查看的会话。
        已点击查看（acked）的不再出现；按最近活动排序，最多 6 张。"""
        out = [(sid, s) for sid, s in self.sess.items()
               if s["state"] in ("thinking", "executing", "waiting")
               or (s["state"] == "done" and not s.get("acked"))
               or (s["task"] and not s.get("acked"))]
        out.sort(key=lambda kv: -kv[1]["last_ts"])
        return out[:BubbleWindow.MAX_CARDS]

    def _refresh_bubble(self):
        entries = self._card_entries()
        if not self.bubble_expanded:
            # 折叠：聚合优先级最高的卡 + 其他会话中未查看的完成卡
            # （完成卡等用户点击，不因其他会话活跃而被挤掉）
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
        """日志落盘（pet.log），不占桌面 UI。"""
        try:
            if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512 * 1024:
                open(LOG_PATH, "w", encoding="utf-8").close()
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write("{0} [{1}] {2}\n".format(time.strftime("%H:%M:%S"), kind, msg))
        except Exception:
            pass

    # ---------- 演示 ----------
    def run_demo(self):
        self._hide_bubble()
        t0 = 400
        for delay, payload in DEMO_SEQUENCE:
            QTimer.singleShot(t0 + delay, lambda p=payload: self.handle_event(dict(p)))

    # ---------- 退出 ----------
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


# ========== 演示脚本 ==========
DEMO_SEQUENCE = [
    (0,     {"hook_event_name": "SessionStart", "session_id": "demo-0001", "source": "startup"}),
    (900,   {"hook_event_name": "UserPromptSubmit", "session_id": "demo-0001",
             "prompt": "修复登录页在 1080p 下的按钮折行问题，并补充回归测试"}),
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


# ========== 自检：离屏渲染各状态 PNG ==========
def run_selftest():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication(sys.argv)
    try:
        from PyQt5.QtGui import QFontDatabase
        for f in ("consola.ttf", "segoeui.ttf", "segoeuib.ttf", "arial.ttf",
                  "arialbd.ttf", "msyh.ttc", "msyhbd.ttc", "simhei.ttf"):
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
        pm = pet.grab()
        pm.save(os.path.join(out, name + ".png"))

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
    snap_bubble("4-waiting-bubble")   # 横幅气泡单独截图
    pet._hide_bubble()
    snap("4-waiting")
    pet.handle_event({"hook_event_name": "PostToolUse", "session_id": "st", "tool_name": "Edit"})
    pet.handle_event({"hook_event_name": "Stop", "session_id": "st"})
    snap("5-done")
    pet.handle_event({"hook_event_name": "UserPromptSubmit", "session_id": "remote-1",
                      "sessionId": "remote-1", "cwd": "/home/sunhaiming/proj",
                      "prompt": "远程任务：重构 utils/date.py 并跑通单元测试"})
    pet.handle_event({"hook_event_name": "PreToolUse", "sessionId": "remote-1",
                      "toolName": "Bash", "tool_input": {"command": "pytest -q"}})
    pet._show_bubble()
    snap_bubble("6-bubble")            # 折叠态：单卡
    pet.bubble_expanded = True
    pet._refresh_bubble()
    snap_bubble("7-expanded")          # 展开态：远程执行中 + 本机已完成未查看
    pet._on_card_click("st")           # 点击已完成卡 → 跳转并移除
    snap_bubble("8-after-ack")         # 只剩远程执行中的卡
    pet.handle_event({"hook_event_name": "Stop", "sessionId": "remote-1"})
    pet._on_card_click("remote-1")     # 最后一张卡也被查看 → 气泡自动收起
    assert not pet.bwin.isVisible(), "所有卡片查看后气泡应自动收起"
    pet.bubble_expanded = False
    print("SELFTEST_OK " + out)
    return 0


# ========== 入口 ==========
def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ZCode Pet — 任务进度桌宠")
    ap.add_argument("--demo", action="store_true", help="启动后自动演示一轮任务")
    ap.add_argument("--selftest", action="store_true", help="离屏渲染各状态 PNG 后退出")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="事件服务端口（默认 8765）")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(run_selftest())

    # 刻意不启用 Qt 自动高 DPI 缩放（AA_EnableHighDpiScaling）：
    # 双屏 DPI 不同时它会反复重算窗口尺寸，导致桌宠忽大忽小。
    # 关闭后按固定物理像素渲染——在任何屏幕上大小一致（与外接屏一致）。
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setApplicationName("ZCode Pet")

    lock = QLockFile(os.path.join(tempfile.gettempdir(), "zcode-pet.lock"))
    if not lock.tryLock(0):
        # 已有实例在跑：让它显示并置顶（双击 bat 的意义就在于"看到它"）
        try:
            import urllib.request
            for p in range(8765, 8770):
                try:
                    urllib.request.urlopen("http://127.0.0.1:{0}/show".format(p),
                                           timeout=0.6)
                    break
                except Exception:
                    continue
        except Exception:
            pass
        print("ZCode Pet 已在运行（已请求显示）。")
        sys.exit(0)

    pet = PetWindow(server=True, tray=True, hotkey=True, port=args.port)
    pet.show()
    if args.demo:
        QTimer.singleShot(800, pet.run_demo)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
