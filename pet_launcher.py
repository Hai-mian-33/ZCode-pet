# -*- coding: utf-8 -*-
# Author: Sun Haiming (孙海铭) · https://github.com/Hai-mian-33
"""
ZCode Pet 拉起器 —— 由本机钩子在 SessionStart / UserPromptSubmit 时调用。
桌宠已在运行（8765~8769 任一端口有响应）→ 立即退出；
未运行 → 用 pythonw 静默拉起 zcode_pet.py（无黑窗口）。
永远 0 秒退出、永不报错，不会阻塞 ZCode 的任何操作。
"""
import os
import socket
import subprocess
import sys
import time

PORT_RANGE = range(8765, 8770)
BASE = os.path.dirname(os.path.abspath(__file__))
PET = os.path.join(BASE, "zcode_pet.py")


def pet_running():
    for port in PORT_RANGE:
        s = socket.socket()
        s.settimeout(0.25)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
    return False


def start_pet():
    py = sys.executable or "pythonw"
    if os.path.basename(py).lower() != "pythonw.exe":
        cand = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.exists(cand):
            py = cand
    flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    subprocess.Popen([py, PET], close_fds=True, creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=BASE)


def main():
    try:
        if os.path.exists(PET) and not pet_running():
            start_pet()
            time.sleep(0.2)  # 稍等，避免同一批钩子里重复拉起
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
