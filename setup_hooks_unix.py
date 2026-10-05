#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Author: Sun Haiming (孙海铭) · https://github.com/Hai-mian-33
"""
ZCode Pet hooks 安装/卸载（Linux/macOS 远程服务器版）
=====================================================
在远程服务器上运行，把 ZCode hooks 写入 ~/.zcode/cli/config.json。
事件经 SSH 反向隧道（RemoteForward 8765）回传到你 Windows 桌面上的桌宠。

v2: 改用 type:"command"（shell 管道传 stdin，兼容性最好）；
    curl 超时放宽到 4s；`|| true` 保证钩子永不阻塞远程任务。
    安装时自动清掉 v1 的 process 型旧钩子。

用法：
    python3 setup_hooks_unix.py            # 安装（自动备份、幂等）
    python3 setup_hooks_unix.py --remove   # 卸载
    python3 setup_hooks_unix.py --show     # 查看当前配置
"""
import json
import os
import sys
import shutil
import time
import argparse

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, ".zcode", "cli", "config.json")

# 经 SSH 反向隧道回到桌宠（127.0.0.1:8765 在服务器上即隧道入口）
PET_URL = "http://127.0.0.1:8765/event"
MARK_PREFIX = "zcode-pet"  # 识别/清理所有版本的 zcode-pet 钩子

EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
          "PostToolUse", "PostToolUseFailure", "Stop"]

HOOK_CMD = ("curl -s -S -m 4 -X POST {url} --data-binary @- || true".format(url=PET_URL))


def _make_hook():
    return {
        "type": "command",
        "command": HOOK_CMD,
        "timeoutMs": 6000,
        "statusMessage": "zcode-pet-v2",
    }


def _is_ours(entry):
    if not isinstance(entry, dict):
        return False
    return any(isinstance(h, dict)
               and str(h.get("statusMessage", "")).startswith(MARK_PREFIX)
               for h in entry.get("hooks", []) or [])


def _load():
    if not os.path.exists(CONFIG):
        return {}
    with open(CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def install():
    cfg = _load()
    if os.path.exists(CONFIG):
        ts = time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(CONFIG, CONFIG + ".bak-zcodepet-" + ts)
    hooks = cfg.get("hooks") if isinstance(cfg.get("hooks"), dict) else {}
    hooks["enabled"] = True
    events = hooks.setdefault("events", {})
    added, replaced = 0, 0
    for ev in EVENTS:
        lst = events.setdefault(ev, [])
        old = len(lst)
        # 清掉任意版本的 zcode-pet 旧钩子，再装当前版
        lst = [e for e in lst if not _is_ours(e)]
        replaced += old - len(lst)
        lst.append({"hooks": [_make_hook()]})
        events[ev] = lst
        added += 1
    cfg["hooks"] = hooks
    _save(cfg)
    print("OK hooks written to {0} ({1} events, removed {2} old, enabled=true)"
          .format(CONFIG, added, replaced))
    print("   next: restart ZCode sessions on this server to load hooks")


def remove():
    if not os.path.exists(CONFIG):
        print("no config file, nothing to remove")
        return
    with open(CONFIG, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    hooks = cfg.get("hooks")
    removed = 0
    if isinstance(hooks, dict):
        events = hooks.get("events")
        if isinstance(events, dict):
            for ev in EVENTS:
                lst = events.get(ev)
                if not isinstance(lst, list):
                    continue
                kept = [e for e in lst if not _is_ours(e)]
                removed += len(lst) - len(kept)
                if kept:
                    events[ev] = kept
                else:
                    del events[ev]
            if not events:
                hooks.pop("events", None)
        if not any(k != "enabled" for k in hooks):
            cfg.pop("hooks", None)
        _save(cfg)
    print("OK removed {0} zcode-pet hooks".format(removed))


def show():
    if not os.path.exists(CONFIG):
        print("config not found: {0}".format(CONFIG))
        return
    with open(CONFIG, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    hooks = cfg.get("hooks") or {}
    events = hooks.get("events") or {}
    print("config: {0}  enabled={1}".format(CONFIG, hooks.get("enabled")))
    for ev in EVENTS:
        lst = events.get(ev) or []
        ours = sum(1 for e in lst if _is_ours(e))
        print("  {0:<20} {1} hooks (zcode-pet: {2})".format(ev, len(lst), ours))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    if a.show:
        show()
    elif a.remove:
        remove()
    else:
        install()
