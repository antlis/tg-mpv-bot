# tg-mpv-bot 🎬

[![CI](https://github.com/antlis/tg-mpv-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/antlis/tg-mpv-bot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Turn Telegram into a remote control for **mpv** on your desktop/HTPC: browse
your media library with inline buttons, stream any YouTube/SoundCloud/… link
by just sending it, seek/pause/volume from your phone. No AI, no cloud — the
bot talks to mpv's JSON IPC socket directly from Python.

- 📋 **Browse** playlists by category with inline keyboards, or search
- 🔗 **Stream URLs** — send a link, mpv plays it via yt-dlp (1000+ sites)
- 📨 **Send a file** — forward any video/audio from Telegram, it plays on the TV
- 🎛 **Now-playing panel** — transport, seek-to-%, volume, tracks, one message
- 📜 **Episode picker**, ▶ **continue watching**, 📸 **frame screenshots**
- 🪝 **Hooks** instead of WM assumptions — `i3-msg`/`swaymsg`/`notify-send`, your call

![tg-mpv-bot demo](docs/demo.svg)

## The use case

A home server (or any always-on Linux box) is plugged into the TV over HDMI.
The bot runs on that box; **your phone — or any device with Telegram — is the
remote.**

```
   phone / laptop                home server ──HDMI──▶ TV
  ┌──────────────┐   Telegram   ┌─────────────────────────┐
  │  @your_bot   │ ───────────▶ │  tg-mpv-bot ──▶ mpv ────┼──▶ 📺
  │  /mpv_list   │              │  (X11/Wayland session)  │
  └──────────────┘              └─────────────────────────┘
```

From the couch: open Telegram, `/mpv_list`, tap a show — mpv opens fullscreen
on the TV. Send a YouTube link from your phone's share sheet — it streams on
the TV. Pause from the now-playing panel when the doorbell rings, drag the
volume, switch the audio track or subtitles, jump to episode 7 — all without
a keyboard, mouse, or smart-TV apps. `/mpv_last` resumes yesterday's episode
where you left off.

Because it's Telegram, the "remote" works from anywhere — same couch or other
side of the world — with no ports forwarded, no VPN, no local network setup:
the bot makes only outbound connections. `ALLOWED_USERS` keeps it yours.

## Commands

| Command | Description |
|---------|-------------|
| `/mpv_list` | Browse playlists with inline buttons (by category) |
| `/mpv_play <query>` | Search & play by name or number |
| `/mpv <query>` | Alias for `/mpv_play` |
| `/mpv_search [category] <text>` | List all matching playlists as play buttons (e.g. `/mpv_search tutorials docker`) |
| `/mpv_last` | Resume the last-played playlist or stream (position restored by mpv) |
| `/mpv_history` | Last 20 played (playlists & streams) — tap to replay |
| `/mpv_notify` | Toggle notifications: "⏭ Now playing: 5/12 — …" on episode change, "✅ Finished" at the end |
| `/mpv_url <link>` | Stream a URL via yt-dlp — or just **send a link** as a message |
| `/mpv_yt <search>` | Search YouTube from chat — top results as tap-to-play buttons |
| *(send a video/audio file)* | Downloads and plays it — >20 MB needs `API_SERVER_URL` (local Bot API server) |
| `/mpv_info` | Now-playing panel with inline transport buttons |
| `/mpv_shot` | Send a screenshot of the current frame to the chat |
| `/mpv_toggle` | Play/pause toggle (one command) |
| `/mpv_pause` | Pause playback |
| `/mpv_unpause` | Resume playback |
| `/mpv_quit` | Stop mpv and quit |
| `/mpv_fwd` | Seek forward 30s |
| `/mpv_back` | Seek backward 10s |
| `/mpv_goto <pos>` | Seek to `1:23:45`, `23:45`, `90` (seconds) or `75%` |
| `/mpv_next` | Next item in playlist |
| `/mpv_prev` | Previous item in playlist |
| `/mpv_ep [n]` | Episode picker with buttons (no arg) or jump to item N |
| `/mpv_speed [x]` | Playback speed — buttons (no arg) or a value like `1.5` |
| `/mpv_shuffle` | Shuffle the current playlist |
| `/mpv_loop` | Toggle looping the playlist |
| `/mpv_sleep <time>` | Sleep timer — stop playback after `45m` / `1.5h` (`off` to cancel) |
| `/mpv_random [category]` | Play a random playlist — "just put something on" |
| `/mpv_night` | Toggle loudness normalization (quiet dialogue ↑, explosions ↓) |
| `/mpv_audio` | Switch to the next audio track (e.g. Spanish → English) |
| `/mpv_sub` | Switch to the next subtitle track |
| `/mpv_sub_toggle` | Show / hide subtitles |
| `/mpv_volup` | Volume +10 |
| `/mpv_voldown` | Volume -10 |
| `/mpv_mute` | Toggle mute |
| `/mpv_doctor` | Report playlists with missing files on disk |
| `/mpv_fix` | Repair broken playlists (re-point moved files, prune dead) |
| `/mpv_scan` | Create playlists for newly-added media (idempotent) |
| `/mpv_update_ytdlp` | Update the bot's yt-dlp to the latest nightly — the usual fix when YouTube playback breaks |
| `/help` | Show this help |

## Setup

### 1. Prerequisites

- Linux box with a graphical session (X11 or Wayland) whose display is the TV
- `mpv`
- Python 3.11+
- `yt-dlp` — only needed for URL streaming / YouTube search / Telegram files.
  Install it **into the bot's venv** (the bot prefers that copy, and
  `/mpv_update_ytdlp` updates it): YouTube breaks extraction every few months
  and distro packages lag, so use the nightly.

### 2. Create the bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Get your numeric Telegram ID from [@userinfobot](https://t.me/userinfobot).

### 3. Install & first run

```bash
git clone https://github.com/antlis/tg-mpv-bot && cd tg-mpv-bot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install -U "https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/latest/download/yt-dlp.tar.gz"

cp .env.example .env        # set BOT_TOKEN and ALLOWED_USERS at minimum
set -a; source .env; set +a
python bot.py
```

Message your bot `/help` — if it answers, the Telegram side works. Then
`/mpv_list` to browse, or send any YouTube link.

> **Access control:** leave `ALLOWED_USERS` empty and *anyone* who finds the
> bot can control your TV. Set it.

### 4. Your media library

The browse UI expects this layout (category → playlists, with one optional
nesting level for subcategories):

```
~/Videos/
├── movie/
│   └── playlists/
│       ├── Fight Club.m3u
│       └── Heat (1995).m3u
├── shows/
│   └── playlists/
│       └── Deadwood S01.m3u
└── tutorials/
    └── playlists/
        └── frontend-masters/        ← subcategory
            └── Advanced CSS.m3u
```

- Categories = the directory names under `VIDEOS_DIR` (anything you like —
  the four above are just the defaults; set `PLAYLIST_DIRS` for a custom set).
- `.m3u` files are plain lists of media paths (absolute, or relative to the
  playlist's own directory).
- **Don't want to write playlists by hand?** Drop media files/folders under a
  category and run `/mpv_scan` — it generates one playlist per folder (or per
  loose file), idempotently. `/mpv_doctor` reports broken entries after you
  move things; `/mpv_fix` repairs them.
- No library at all is fine too — URL streaming, YouTube search and Telegram
  file playback work without one.

### 5. Run it permanently

**Systemd user service** (recommended on a host you own):

```bash
ln -s "$PWD/tg-mpv-bot.service" ~/.config/systemd/user/
mkdir -p ~/.config/environment.d
cat > ~/.config/environment.d/99-tg-mpv-bot.conf << EOF
BOT_TOKEN=your_token_here
ALLOWED_USERS=123456789
EOF
systemctl --user daemon-reload
systemctl --user enable --now tg-mpv-bot
journalctl --user-unit tg-mpv-bot -f     # logs
```

The unit assumes the repo at `~/Projects/tg-mpv-bot` with a `venv/` inside —
edit `WorkingDirectory`/`ExecStart` if yours lives elsewhere. Tweak the
`Environment=` lines (hooks, yt-dlp options) in the unit itself.

**Docker:**

```bash
docker compose up -d --build
```

Uses `network_mode: host` and bind-mounts the X11 socket, the mpv IPC socket
and your playlist dirs (paths in `docker-compose.yml`). Note hooks run
*inside* the container — add the tools they call to the image.

> ⚠️ Run exactly **one** instance per bot token (the lock file guards one
> host, but Telegram allows only one poller globally — a second instance
> elsewhere causes `TelegramConflictError`).

## Configuration

Everything is configured via environment variables (`.env` for bare runs,
`environment.d` for systemd, `environment:` for Docker). Only `BOT_TOKEN` is
required.

| Variable | Default | Purpose |
|----------|---------|---------|
| `BOT_TOKEN` | — *(required)* | Bot token from @BotFather |
| `ALLOWED_USERS` | *(empty = open!)* | Comma-separated Telegram user IDs allowed to use the bot |
| `VIDEOS_DIR` | `~/Videos` | Library root — categories are its subdirectories |
| `PLAYLIST_DIRS` | `$VIDEOS_DIR/{cartoons,movie,shows,tutorials}/playlists` | Explicit playlist dirs (`:`-separated) if your layout differs |
| `MPV_SOCKET` | `/tmp/mpv-socket` | mpv JSON IPC socket the bot creates/controls |
| `DISPLAY` | `:0` | X11 display the mpv window opens on |
| `MPV_RUNNER` | `/tmp/mpv-runner.sh` | Optional wrapper script to launch instead of `mpv` (plain `mpv` when absent) |
| `PRE_PLAY_HOOK` | *(none)* | Shell command run before mpv starts — WM glue like `i3-msg workspace 10`; sees `$PLAYLIST`, `$PLAYLIST_NAME`, `$MPV_SOCKET`, `$DISPLAY` |
| `POST_PLAY_HOOK` | *(none)* | Same, run right after the mpv spawn |
| `KILL_STRAY_MPV` | `1` | Also `pkill` mpv instances the bot didn't start; `0` if you use mpv manually too |
| `YTDL_FORMAT` | `bv*[height<=1080]+ba/b` | yt-dlp format for URL streaming (raise the cap for 4K) |
| `YTDL_OPTIONS` | *(none)* | Extra yt-dlp options, comma-separated `key=value` / bare flags — e.g. `force-ipv4` or the lean-YouTube `extractor-args=…` (see `.env.example`) |
| `YTDL_COOKIES_BROWSER` | *(none)* | Browser whose cookies unlock Instagram/Facebook and YouTube bot-checks (e.g. `firefox`); applied only to gated hosts / as an escalation, never globally |
| `API_SERVER_URL` | *(none)* | Local [Bot API server](https://github.com/tdlib/telegram-bot-api) — lifts the 20 MB download cap to 2 GB for sent files (one-time `…/logOut` from the cloud API required when switching) |
| `API_LOCAL_FILES_DIR` | *(none)* | Host path of the server's `/var/lib/telegram-bot-api` when it runs with `TELEGRAM_LOCAL=true` — the bot then reads downloaded files straight from disk |
| `SCAN_INTERVAL_MIN` | `0` | If >0, auto-run the playlist generator every N minutes |
| `YTDL_UPDATE_DAYS` | `0` | If >0, auto-update yt-dlp every N days (recommended: `7`) and report version bumps in chat |
| `STATE_FILE` | `~/.local/state/tg-mpv-bot/state.json` | Watch history / notification target |
| `LOCK_FILE` | `/tmp/tg-mpv-bot.lock` | Single-instance lock |

## Tests & lint

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
```

## Files

| Path | Purpose |
|------|---------|
| `bot.py` | Entry point — aiogram polling bot + auth middleware |
| `src/config.py` | Settings from env (single source of host paths) |
| `src/commands.py` | Telegram command + callback handlers |
| `src/mpv_ipc.py` | Direct JSON-IPC client for mpv (pause/seek/volume/info) |
| `src/playlists.py` | Playlist discovery, query matching, on-disk validation |
| `src/player.py` | Launch mpv (pkill + pre/post-play hooks + detached spawn) |
| `src/keyboards.py` | Inline-keyboard builders for browsing |
| `docker-compose.yml` | Docker deployment (host networking + X11 bind) |
| `Dockerfile` | Container build (Python 3.12 + mpv) |

## Architecture

```
                          ┌─ src/mpv_ipc  ──▶ mpv JSON IPC (/tmp/mpv-socket)   pause/seek/vol/info
Telegram ─▶ bot.py ─▶ src/commands ─┤
            (polling)   (+ auth mw)  ├─ src/playlists ──▶ scan ~/Videos/*/playlists/*.m3u
                                     └─ src/player   ──▶ pkill mpv · pre-hook · spawn mpv · post-hook ─▶ X11
```

Playback/volume/info commands write straight to mpv's IPC socket from Python.
Only launching a playlist (`/mpv_play`, tapping a button) spawns a process.

Window-manager glue is **not** built in — set the optional hooks instead
(shell commands; they see `$PLAYLIST`, `$PLAYLIST_NAME`, `$MPV_SOCKET`,
`$DISPLAY`):

```bash
PRE_PLAY_HOOK="i3-msg workspace 10"          # i3: jump to the media workspace
PRE_PLAY_HOOK="swaymsg workspace 10"         # sway
PRE_PLAY_HOOK="hyprctl dispatch workspace 10"  # Hyprland
POST_PLAY_HOOK='notify-send "Now playing" "$PLAYLIST_NAME"'
```

Hook failures are logged and never block playback (15s timeout).
`src/keyboards` renders the browse UI for `/mpv_list`: **category → (subcategory)
→ playlist**. Categories come from the top-level media dirs (cartoons / movie /
shows / tutorials); a playlists dir may nest one level of folders, which become
subcategories (tutorials are grouped by provider, e.g. `frontend-masters`).

## License

[MIT](LICENSE)
