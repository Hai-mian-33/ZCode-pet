# -*- coding: utf-8 -*-
# Author: Sun Haiming (孙海铭) · https://github.com/Hai-mian-33
"""
Regenerate docs/screenshots/*.png offscreen (no window flashes).

Usage:
    python tools/gen_screenshots.py

Captures every pet state (idle / thinking / executing / waiting / done),
the task-card stack in both UI languages, and composes a hero banner.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from PyQt5.QtCore import Qt, QRect
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPainter, QPixmap, QLinearGradient
from PyQt5.QtWidgets import QApplication

import zcode_pet as zp

OUT = os.path.join(ROOT, "docs", "screenshots")

FIXTURES = {
    "zh": {
        "task":  "修复登录页在 1080p 下的按钮折行问题，并补充回归测试",
        "result": "已修复按钮折行，并新增 2 个回归测试",
        "task2": "远程任务：重构 utils/date.py 并跑通单元测试",
        "labels": ["空闲", "思考中", "执行中", "等你确认", "已完成"],
        "cards_title": "任务卡片堆（中文界面）",
    },
    "en": {
        "task":  "Fix the login-page button wrapping at 1080p and add regression tests",
        "result": "Fixed the wrapping and added 2 regression tests",
        "task2": "Remote task: refactor utils/date.py and make the tests pass",
        "labels": ["Idle", "Thinking", "Executing", "Waiting for you", "Done"],
        "cards_title": "Task card stack (English UI)",
    },
}


def make_app():
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        for f in ("consola.ttf", "segoeui.ttf", "segoeuib.ttf", "arial.ttf",
                  "arialbd.ttf", "seguisym.ttf", "segoeui-symbol.ttf", "msyh.ttc", "msyhbd.ttc", "simhei.ttf"):
            QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + f)
    except Exception:
        pass
    return app


def fresh_pet():
    pet = zp.PetWindow(server=False, tray=False, hotkey=False)
    pet.show()
    return pet


def pump(app, pet, n=3):
    for _ in range(n):
        app.processEvents()


def snap_widget(widget, path):
    pump_needed = False
    widget.grab().save(path)
    return path


def run_lang(app, lang):
    fx = FIXTURES[lang]
    zp.CURRENT_LANG = lang
    pet = fresh_pet()
    shots = {}

    pet.face.tick = 12
    pump(app, pet)
    shots["pet-idle"] = pet.grab()

    pet.handle_event({"hook_event_name": "UserPromptSubmit", "session_id": "demo",
                      "prompt": fx["task"]})
    pet.face.tick = 26
    pump(app, pet)
    shots["pet-thinking"] = pet.grab()

    pet.handle_event({"hook_event_name": "PreToolUse", "session_id": "demo",
                      "tool_name": "Read",
                      "tool_input": {"file_path": "src/pages/Login.tsx"}})
    pet.handle_event({"hook_event_name": "PostToolUse", "session_id": "demo",
                      "tool_name": "Read"})
    pet.handle_event({"hook_event_name": "PreToolUse", "session_id": "demo",
                      "tool_name": "Edit",
                      "tool_input": {"file_path": "src/pages/Login.tsx"}})
    pet.face.tick = 40
    pump(app, pet)
    shots["pet-executing"] = pet.grab()

    pet.handle_event({"hook_event_name": "PermissionRequest", "session_id": "demo",
                      "tool_name": "Bash",
                      "tool_input": {"command": "git commit -m 'fix: login wrap'"}})
    pet.face.tick = 52
    pump(app, pet)
    shots["pet-waiting"] = pet.grab()
    pet.bwin.popup(pet)
    pump(app, pet)
    shots["bubble-waiting"] = pet.bwin.grab()

    pet.handle_event({"hook_event_name": "PostToolUse", "session_id": "demo",
                      "tool_name": "Edit"})
    pet.handle_event({"hook_event_name": "Stop", "session_id": "demo",
                      "responsePreview": fx["result"]})
    pet.face.tick = 64
    pump(app, pet)
    shots["pet-done"] = pet.grab()

    # ---- second (remote) session → expanded card stack ----
    pet.handle_event({"hook_event_name": "UserPromptSubmit",
                      "sessionId": "remote-1", "cwd": "/home/yourname/proj",
                      "prompt": fx["task2"]})
    pet.handle_event({"hook_event_name": "PreToolUse", "sessionId": "remote-1",
                      "toolName": "Bash",
                      "tool_input": {"command": "pytest -q"}})
    pet.handle_event({"hook_event_name": "PostToolUse", "sessionId": "remote-1",
                      "toolName": "Bash"})
    pet.bubble_expanded = True
    pet._show_bubble()
    pump(app, pet)
    shots["cards-stack"] = pet.bwin.grab()

    for name, pm in shots.items():
        pm.save(os.path.join(OUT, "{0}-{1}.png".format(name, lang)))
    pet._quit()
    return shots


def compose_hero(states, labels, path):
    """Row of pet states on a dark brand-ish background with captions."""
    if not states:
        return
    pm_w, pm_h = states[0].width(), states[0].height()
    margin, gap, label_h, title_h = 24, 18, 26, 44
    n = len(states)
    W = margin * 2 + n * pm_w + (n - 1) * gap
    H = title_h + pm_h + label_h + margin
    canvas = QPixmap(W, H)
    canvas.fill(QColor("#1E1E22"))
    p = QPainter(canvas)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 0, H)
    grad.setColorAt(0.0, QColor("#26262B"))
    grad.setColorAt(1.0, QColor("#1B1B1F"))
    p.fillRect(0, 0, W, H, grad)
    p.setPen(QColor("#FEBC2E"))
    p.setFont(QFont("Segoe UI", 15, QFont.Bold))
    p.drawText(QRect(0, 10, W, 28), Qt.AlignCenter, "ZCode Pet")
    x = margin
    y = title_h + 4
    for pm, label in zip(states, labels):
        p.drawPixmap(x, y, pm)
        p.setPen(QColor("#A1A1A1"))
        p.setFont(QFont("Segoe UI", 10, QFont.Bold))
        p.drawText(QRect(x - 10, y + pm_h + 6, pm_w + 20, label_h), Qt.AlignCenter, label)
        x += pm_w + gap
    p.end()
    canvas.save(path)


def compose_cards(zh_pm, en_pm, path):
    margin, gap = 24, 24
    w = zh_pm.width() + en_pm.width() + margin * 2 + gap
    h = max(zh_pm.height(), en_pm.height()) + margin * 2 + 30
    canvas = QPixmap(w, h)
    grad = QLinearGradient(0, 0, 0, h)
    grad.setColorAt(0.0, QColor("#26262B"))
    grad.setColorAt(1.0, QColor("#1B1B1F"))
    p = QPainter(canvas)
    p.fillRect(0, 0, w, h, grad)
    p.setPen(QColor("#A1A1A1"))
    p.setFont(QFont("Segoe UI", 10, QFont.Bold))
    p.drawPixmap(margin, margin, zh_pm)
    p.drawText(QRect(margin, margin + zh_pm.height() + 6, zh_pm.width(), 22),
               Qt.AlignCenter, "中文")
    x2 = margin + zh_pm.width() + gap
    p.drawPixmap(x2, margin, en_pm)
    p.drawText(QRect(x2, margin + en_pm.height() + 6, en_pm.width(), 22),
               Qt.AlignCenter, "English")
    p.end()
    canvas.save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    app = make_app()
    zh = run_lang(app, "zh")
    en = run_lang(app, "en")
    compose_hero([zh["pet-idle"], zh["pet-thinking"], zh["pet-executing"],
                  zh["pet-waiting"], zh["pet-done"]],
                 FIXTURES["zh"]["labels"],
                 os.path.join(OUT, "hero.png"))
    compose_hero([en["pet-idle"], en["pet-thinking"], en["pet-executing"],
                  en["pet-waiting"], en["pet-done"]],
                 FIXTURES["en"]["labels"],
                 os.path.join(OUT, "hero-en.png"))
    compose_cards(zh["cards-stack"], en["cards-stack"],
                  os.path.join(OUT, "cards.png"))
    print("SCREENSHOTS_OK " + OUT)


if __name__ == "__main__":
    main()
