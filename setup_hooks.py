# -*- coding: utf-8 -*-
"""
ZCode Pet hooks 安装/卸载脚本
==============================
把 ZCode 官方 hooks 写入用户级配置 ~/.zcode/cli/config.json，
让 ZCode 在会话事件发生时把 JSON 推给桌宠（127.0.0.1:8765/event）。

用法：
    python setup_hooks.py            # 安装（自动备份、幂等，可重复执行）
    python setup_hooks.py --remove   # 卸载（只移除本脚本添加的钩子）
    python setup_hooks.py --show     # 查看当前 hooks 配置

说明：
- ZCode 配置文件钩子默认禁用，本脚本会设置 hooks.enabled: true。
- 钩子使用 type:"process"（无 shell，跨平台最稳），命令为 System32 curl.exe。
- 额外携带一个「拉起器」钩子（SessionStart / UserPromptSubmit）：
  桌宠未运行时自动用 pythonw 静默拉起——你一在 ZCode 里开会话/提交任务，
  桌宠就自动出现（pet_launcher.py，毫秒级、永不阻塞）。
- 桌宠未运行时 curl 连接被拒立即失败（毫秒级），只会在 ZCode 日志里
  记一条 hook 失败，不会阻塞或影响 ZCode 的任何操作。
- 安装后需要新开的 ZCode 会话才会加载钩子。
"""
import json
import os
import sys
import shutil
import time
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HOME = os.path.expanduser("~")
CONFIG = os.path.join(HOME, ".zcode", "cli", "config.json")

# 与 zcode_pet.py 中的 DEFAULT_PORT 保持一致（改端口要两边同步改）
PET_URL = "http://127.0.0.1:8765/event"
MARK = "zcode-pet"  # 写在 statusMessage 里，用于识别/卸载本脚本添加的钩子
LAUNCH_MARK = "zcode-pet-launch"
LAUNCH_EVENTS = ["SessionStart", "UserPromptSubmit"]
LAUNCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pet_launcher.py")

# ZCode 支持且仅支持这 7 个钩子事件
EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PermissionRequest",
          "PostToolUse", "PostToolUseFailure", "Stop"]


def _curl_exe():
    """优先用 System32 自带 curl（无 PATH 依赖）。"""
    for cand in (r"C:\Windows\System32\curl.exe", "/mingw64/bin/curl", "curl"):
        if cand in ("curl",) or os.path.exists(cand):
            return cand
    return "curl"


def _pythonw():
    exe = sys.executable or ""
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else (exe or "pythonw")


def _make_hook():
    return {
        "type": "process",
        "command": _curl_exe(),
        "args": ["-s", "-S", "-m", "1", "-X", "POST", PET_URL, "--data-binary", "@-"],
        "timeoutMs": 2000,
        "statusMessage": MARK,
    }


def _make_launcher_hook():
    return {
        "type": "process",
        "command": _pythonw(),
        "args": [LAUNCHER],
        "timeoutMs": 5000,
        "statusMessage": LAUNCH_MARK,
    }


def _entry_has_mark(entry, marks):
    if not isinstance(entry, dict):
        return False
    return any(isinstance(h, dict) and str(h.get("statusMessage", "")) in marks
               for h in entry.get("hooks", []) or [])


def _entry_is_zcodepet(entry):
    return _entry_has_mark(entry, {MARK, LAUNCH_MARK})


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


def _backup():
    if os.path.exists(CONFIG):
        ts = time.strftime("%Y%m%d-%H%M%S")
        dst = CONFIG + ".bak-zcodepet-" + ts
        shutil.copy2(CONFIG, dst)
        return dst
    return None


def install():
    cfg = _load()
    bak = _backup()
    hooks = cfg.get("hooks") if isinstance(cfg.get("hooks"), dict) else {}
    hooks["enabled"] = True  # 配置文件钩子默认禁用，必须显式开启
    events = hooks.setdefault("events", {})
    added = []
    for ev in EVENTS:
        lst = events.setdefault(ev, [])
        # 清掉任意旧版 zcode-pet 钩子后统一重建（保留其他来源的钩子）
        lst = [e for e in lst if not _entry_is_zcodepet(e)]
        hook_list = [_make_hook()]
        if ev in LAUNCH_EVENTS and os.path.exists(LAUNCHER):
            hook_list.append(_make_launcher_hook())
        lst.append({"hooks": hook_list})
        events[ev] = lst
        added.append(ev)
    cfg["hooks"] = hooks
    _save(cfg)
    print("✓ 已写入 {0}".format(CONFIG))
    if bak:
        print("  原文件已备份到 {0}".format(bak))
    print("  事件钩子: {0} 个事件（SessionStart / UserPromptSubmit 额外携带".format(len(added)))
    print("  「拉起器」：桌宠未运行时随 ZCode 自动静默启动）")
    print("  hooks.enabled = true")
    print("\n下一步：")
    print("  1. 启动桌宠      python zcode_pet.py （或双击 ZCodePet.bat）")
    print("  2. 新开 ZCode 会话提交任务即可看到进度；桌宠被关掉时，")
    print("     下一次在 ZCode 里活动会自动拉起它")


def remove():
    if not os.path.exists(CONFIG):
        print("配置文件不存在，无需卸载。")
        return
    with open(CONFIG, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    bak = _backup()
    hooks = cfg.get("hooks")
    removed = 0
    if isinstance(hooks, dict):
        events = hooks.get("events")
        if isinstance(events, dict):
            for ev in EVENTS:
                lst = events.get(ev)
                if not isinstance(lst, list):
                    continue
                kept = [e for e in lst if not _entry_is_zcodepet(e)]
                removed += len(lst) - len(kept)
                if kept:
                    events[ev] = kept
                else:
                    del events[ev]
            if not events:
                hooks.pop("events", None)
        if not any(k != "enabled" for k in hooks):
            cfg.pop("hooks", None)  # 只剩 enabled 时整个移除，不留残渣
        _save(cfg)
    print("✓ 已移除 {0} 个 zcode-pet 钩子（含拉起器）".format(removed))
    if bak:
        print("  卸载前配置已备份到 {0}".format(bak))


def show():
    if not os.path.exists(CONFIG):
        print("配置文件不存在：{0}".format(CONFIG))
        return
    with open(CONFIG, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    hooks = cfg.get("hooks") or {}
    print("配置文件: {0}".format(CONFIG))
    print("hooks.enabled: {0}".format(hooks.get("enabled")))
    events = hooks.get("events") or {}
    for ev in EVENTS:
        lst = events.get(ev) or []
        pet = sum(1 for e in lst if _entry_has_mark(e, {MARK}))
        launch = sum(1 for e in lst if _entry_has_mark(e, {LAUNCH_MARK}))
        extra = "  ←含拉起器" if launch else ""
        print("  {0:<20} {1} 条（zcode-pet: {2}）{3}".format(ev, len(lst), pet, extra))


def main():
    ap = argparse.ArgumentParser(description="ZCode Pet hooks 安装/卸载")
    ap.add_argument("--remove", action="store_true", help="卸载钩子")
    ap.add_argument("--show", action="store_true", help="查看当前 hooks 配置")
    args = ap.parse_args()
    if args.show:
        show()
    elif args.remove:
        remove()
    else:
        install()


if __name__ == "__main__":
    main()
