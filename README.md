<div align="center">

# 🟡 ZCode Pet

**A task-progress desktop pet for [ZCode](https://z.ai) — your AI coding sessions, alive on your desktop.**

[中文说明](README.zh-CN.md) · English · **Author: [@Hai-mian-33](https://github.com/Hai-mian-33)**

`Windows` `Python 3.9+` `PyQt5` `MIT License`

<img src="docs/screenshots/hero.png" width="620" alt="ZCode Pet states" />

</div>

---

ZCode Pet is an anthropomorphized version of the ZCode "Z" logo that lives on your
desktop and **reflects what your ZCode sessions are actually doing in real time**:
it thinks when you submit a task, shows every tool call with a step counter,
**nags you when ZCode waits for your permission**, celebrates with a ✓ when a task
finishes — and can be clicked to jump straight back into ZCode.

It works with **local ZCode sessions and remote ones over SSH** (via reverse tunnels),
tracks multiple concurrent sessions without mixing them up, and speaks
**Chinese and English** (switchable in the right-click menu).

## ✨ Features

- 🎯 **Real progress, not a toy** — driven by ZCode official hooks
  (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`,
  `PostToolUse`, `PostToolUseFailure`, `Stop`); supports both `snake_case` and
  `camelCase` payload styles.
- 🃏 **Foldable card stack** — one card per session: task title, live status,
  step counter, elapsed time; completed cards stay pinned with a green ✓ and the
  result summary **until you click them**, then they disappear for good.
- 🐾 **Click to jump** — click the pet to focus the ZCode window; click a card
  to jump to that conversation.
- 📌 **Edge aware** — the pet can never be dragged off-screen; cards flip below
  the pet when there is no room above.
- 🔁 **Follows ZCode's lifecycle** — appears when ZCode starts (hook launcher),
  exits when the ZCode process closes (built-in process monitor).
- 🖥 **Remote friendly** — keeps SSH reverse tunnels alive (see `tunnels.txt`)
  so tasks running on your Linux servers show up too; remote cards are tagged.
- 🌏 **Bilingual** — Chinese / English UI, switchable at runtime, persisted.
- 🔇 **Zero noise** — detailed logs go to `pet.log`, the desktop stays clean.

## 📸 Screenshots

![](docs/screenshots/hero.png)
*Idle · Thinking · Executing · Waiting for your confirmation · Done ✓*

**Task cards** stay pinned above the pet — running tasks and finished tasks
(green ✓ with the result summary) alike — until you click them:

| 中文界面 | English UI |
|---|---|
| ![](docs/screenshots/cards-stack-zh.png) | ![](docs/screenshots/cards-stack-en.png) |

When ZCode waits for your permission, the pet raises a card and a system
notification:

| Pet state | Card |
|---|---|
| ![](docs/screenshots/pet-waiting-zh.png) | ![](docs/screenshots/bubble-waiting-zh.png) |

> All screenshots above are rendered by `tools/gen_screenshots.py` —
> regenerate them any time with `python tools/gen_screenshots.py`.

## 🚀 Quick Start (Windows)

```bat
:: 1. install the dependency
pip install PyQt5

:: 2. install the hooks (writes to ~/.zcode/cli/config.json, auto-backup)
python setup_hooks.py

:: 3. start the pet (no console window)
scripts\ZCodePet.bat
```

Open ZCode, submit any task — the pet appears and starts reporting.

### Linux / macOS servers (remote progress)

```bash
# on each server: install the hook (events travel back over an SSH tunnel)
python3 setup_hooks_unix.py
```

Then list your servers in `tunnels.txt` (copy `tunnels.txt.example`) — the pet
keeps one `ssh -N -R 8765` tunnel alive per server automatically. The reverse
forward is also declared in `~/.ssh/config` by `setup_hooks.py` instructions
below if you prefer a manual setup.

## ⚙️ How it works

```
ZCode session ──hooks──▶ curl POST 127.0.0.1:8765/event ──▶ ZCode Pet
                              │
        remote server ──SSH -R 8765──┘  (tunnels kept alive by the pet)
```

1. `setup_hooks.py` registers one hook per ZCode event. Each hook is a
   `curl` POST of the event JSON to `127.0.0.1:8765` — if the pet is closed,
   the curl fails in milliseconds and **ZCode is never blocked**.
2. A tiny launcher hook (`pet_launcher.py`) silently starts the pet whenever
   ZCode starts and the pet is not running.
3. When the ZCode process closes, the pet's built-in monitor notices and the
   pet exits gracefully.

## 🧩 Repository layout

```
ZCode-pet/
├── zcode_pet.py            # the pet: event server, state machine, rendering
├── pet_launcher.py         # hook helper: start the pet if it isn't running
├── setup_hooks.py          # install/remove hooks on Windows
├── setup_hooks_unix.py     # install/remove hooks on Linux/macOS
├── demo_events.py          # send fake hook events for end-to-end testing
├── autostart.py            # optional: start the pet at Windows login
├── scripts/                # double-click helpers (.bat)
├── tools/gen_screenshots.py# regenerate README screenshots offscreen
├── tunnels.txt.example     # template for remote SSH servers
├── docs/                   # screenshots + bilingual docs
├── README.md / README.zh-CN.md
└── LICENSE
```

## 🌏 Language

Right-click the pet → **Language / 语言** → 中文 / English. The choice is
persisted. You can also set the `PET_LANG=zh|en` environment variable.

## 🛠 Configuration

| File / switch | Purpose |
|---|---|
| `tunnels.txt` | One `user@host[:port]` per line; the pet keeps an SSH reverse tunnel to each server (copy from `tunnels.txt.example`) |
| `PET_LANG=zh\|en` | UI language (default: system language) |
| `scripts/setup_autostart.bat` | Optional: start the pet at Windows login (off by default — the pet follows ZCode's lifecycle) |
| Right-click menu | Fold/expand cards, hide cards, demo task, reset position, language, quit |

## ❓ FAQ

- **Does it block or slow down ZCode?** No. Hooks are millisecond `curl` calls;
  if the pet is closed they fail instantly and silently.
- **How do I uninstall?** `python setup_hooks.py --remove` (also removes the
  launcher), then quit the pet from its tray menu.
- **A hook fired but nothing happened?** Check `pet.log` — every raw event is
  logged there, including `[raw]` payloads and tunnel status.
- **Privacy?** Everything runs locally. The only network traffic is
  `curl` to `127.0.0.1` and the SSH tunnels you configure yourself.

## 👤 Author

**Sun Haiming (孙海铭)** — [@Hai-mian-33](https://github.com/Hai-mian-33) · haiming.sun33@gmail.com

Developer: Sun Haiming. Contributions and issues are welcome!

## 📄 License

[MIT](LICENSE)
