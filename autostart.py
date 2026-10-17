# -*- coding: utf-8 -*-
"""
ZCode Pet 开机自启管理（写入/移除 Windows 登录自启项）
======================================================
用法：
    python autostart.py on      # 开启：登录 Windows 后桌宠自动静默启动
    python autostart.py off     # 关闭
    python autostart.py status  # 查看状态
原理：在「启动」文件夹（shell:startup）放一个 VBS，用 pythonw 无窗口启动桌宠。
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(BASE, "zcode_pet.py")
STARTUP = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                       "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
VBS = os.path.join(STARTUP, "ZCodePet.vbs")


def vbs_content():
    # 0 = 隐藏窗口, False = 不等待
    return 'CreateObject("WScript.Shell").Run "pythonw ""{0}""", 0, False\r\n'.format(TARGET)


def on():
    os.makedirs(STARTUP, exist_ok=True)
    with open(VBS, "w", encoding="ascii") as f:
        f.write(vbs_content())
    print("✓ 已开启开机自启：")
    print("  " + VBS)
    print("  （下次登录 Windows 时桌宠自动启动，无黑窗口）")


def off():
    if os.path.exists(VBS):
        os.remove(VBS)
        print("✓ 已取消开机自启（已删除 " + VBS + "）")
    else:
        print("本来就没有开启自启。")


def status():
    if os.path.exists(VBS):
        print("自启状态：已开启（" + VBS + "）")
    else:
        print("自启状态：未开启")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "on":
        on()
    elif cmd == "off":
        off()
    else:
        status()
