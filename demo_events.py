# -*- coding: utf-8 -*-
"""
ZCode Pet 端到端联调脚本
========================
向正在运行的桌宠（127.0.0.1:8765/event）发送与真实 ZCode hooks 完全同格式的
JSON 事件序列，无需开新 ZCode 会话即可验证整条链路。

用法：
    python zcode_pet.py            # 先启动桌宠
    python demo_events.py          # 另开终端跑本脚本
"""
import json
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8765/event"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SEQ = [
    (0.0,   {"hook_event_name": "SessionStart", "session_id": "e2e-0001", "source": "startup"}),
    (0.8,   {"hook_event_name": "UserPromptSubmit", "session_id": "e2e-0001",
             "prompt": "[e2e] 重构 utils/date.py 并跑通单元测试"}),
    (2.0,   {"hook_event_name": "PreToolUse", "session_id": "e2e-0001",
             "tool_name": "Read", "tool_input": {"file_path": "src/utils/date.py"}}),
    (2.6,   {"hook_event_name": "PostToolUse", "session_id": "e2e-0001", "tool_name": "Read"}),
    (3.4,   {"hook_event_name": "PreToolUse", "session_id": "e2e-0001",
             "tool_name": "Edit", "tool_input": {"file_path": "src/utils/date.py"}}),
    (4.1,   {"hook_event_name": "PostToolUse", "session_id": "e2e-0001", "tool_name": "Edit"}),
    (5.0,   {"hook_event_name": "PreToolUse", "session_id": "e2e-0001",
             "tool_name": "Bash", "tool_input": {"command": "pytest tests/test_date.py -q"}}),
    (5.8,   {"hook_event_name": "PostToolUseFailure", "session_id": "e2e-0001",
             "tool_name": "Bash", "error": "AssertionError: week_start != monday"}),
    (6.9,   {"hook_event_name": "PreToolUse", "session_id": "e2e-0001",
             "tool_name": "Edit", "tool_input": {"file_path": "src/utils/date.py"}}),
    (7.6,   {"hook_event_name": "PostToolUse", "session_id": "e2e-0001", "tool_name": "Edit"}),
    (8.5,   {"hook_event_name": "PermissionRequest", "session_id": "e2e-0001",
             "tool_name": "Bash", "tool_input": {"command": "git commit -m 'refactor: date utils'"}}),
    (11.5,  {"hook_event_name": "PreToolUse", "session_id": "e2e-0001",
             "tool_name": "Bash", "tool_input": {"command": "git commit -m 'refactor: date utils'"}}),
    (12.2,  {"hook_event_name": "PostToolUse", "session_id": "e2e-0001", "tool_name": "Bash"}),
    (13.1,  {"hook_event_name": "PreToolUse", "session_id": "e2e-0001",
             "tool_name": "Bash", "tool_input": {"command": "pytest tests/test_date.py -q"}}),
    (14.0,  {"hook_event_name": "PostToolUse", "session_id": "e2e-0001", "tool_name": "Bash"}),
    (15.2,  {"hook_event_name": "Stop", "session_id": "e2e-0001"}),
]


def send(payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.status


def main():
    print("向 {0} 发送 {1} 个事件（观察桌宠变化）…\n".format(URL, len(SEQ)))
    start = time.time()
    ok = 0
    for delay, payload in SEQ:
        wait = start + delay - time.time()
        if wait > 0:
            time.sleep(wait)
        ev = payload["hook_event_name"]
        try:
            code = send(payload)
            ok += 1
            print("  [{0:5.1f}s] {1:<20} → HTTP {2}".format(delay, ev, code))
        except Exception as e:
            print("  [{0:5.1f}s] {1:<20} → 失败: {2}".format(delay, ev, e))
            print("\n桌宠没在运行？先执行: python zcode_pet.py")
            sys.exit(1)
    print("\n✓ 全部 {0}/{1} 个事件送达（HTTP 204 = 已接收）。".format(ok, len(SEQ)))


if __name__ == "__main__":
    main()
