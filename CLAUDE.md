# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A standalone Telegram bot that remote-controls a local `mpv` instance (play/pause/seek/volume/playlist
selection) over Telegram. No LLM/AI tokens are involved. The bot talks to mpv's JSON IPC socket
directly from Python; only *launching* a playlist spawns a process. Extracted from a larger "Hermes"
bot to run independently.

## Run / develop

```bash
# Bare (uses ./venv per the systemd unit, or any venv)
pip install -r requirements.txt
python bot.py                              # standard Telegram API
API_SERVER_URL=http://localhost:8081 python bot.py   # local Bot API server (2GB uploads)

# Tests + lint
pip install -r requirements-dev.txt
pytest                                     # config (pytest.ini): testpaths=tests, asyncio_mode=auto
ruff check .                               # config in ruff.toml

# Docker (host networking + X11 bind mounts)
docker compose up -d --build

# systemd user service
systemctl --user start tg-mpv-bot          # reads ~/.config/environment.d/99-tg-mpv-bot.conf
```

Required env: `BOT_TOKEN` (missing → `SystemExit` with a friendly message, not a traceback). Optional:
`ALLOWED_USERS` (comma-separated Telegram user IDs — **empty means open to everyone**, logged as a
warning), `API_SERVER_URL`, host paths `MPV_SOCKET` / `PLAYLIST_DIRS` / `VIDEOS_DIR` / `MPV_RUNNER` /
`DISPLAY`, and launch hooks `PRE_PLAY_HOOK` / `POST_PLAY_HOOK` (shell commands run around the mpv
spawn — WM glue like `i3-msg workspace 10` lives there, not in the bot), `YTDL_OPTIONS`
(comma-separated `key=value` passed to mpv as `--ytdl-raw-options` for every URL), and
`YTDL_COOKIES_BROWSER` (browser cookies applied **only** to gated hosts — Instagram/Facebook;
logged-in YouTube cookies make yt-dlp extraction hang, so never make cookies global). See
`.env.example`.

## Architecture

```
                            ┌─ src/mpv_ipc  ─▶ mpv JSON IPC (/tmp/mpv-socket)  pause/seek/vol/info
Telegram ─▶ bot.py ─▶ src/commands ─┤
            (polling)  (+ auth mw)  ├─ src/playlists ─▶ scan ~/Videos/*/playlists/*.m3u
                                    └─ src/player  ─▶ pkill -x mpv · pre-hook · spawn mpv · post-hook ─▶ X11
```

- `bot.py` — entry point. Acquires a single-instance `flock` (`src/lock.py`) before anything else (a
  2nd `python bot.py` exits with "already running" instead of fighting over the token). Builds the
  `Bot` (default `parse_mode=None`), registers the command menu (`_build_menu`), a `@dp.errors()`
  handler (logs + clears the callback spinner), and auth `outer_middleware` on **both** `message` and
  `callback_query` **only when `settings.is_restricted`**. Optionally starts `_scan_loop` when
  `SCAN_INTERVAL_MIN>0`. Switches to a local Bot API server when `API_SERVER_URL` is set.
- `src/config.py` — frozen `Settings` dataclass loaded once via `@lru_cache get_settings()`. **Single
  source of truth for all host paths.** Tests that exercise env must call `get_settings.cache_clear()`.
- `src/mpv_ipc.py` — `MpvClient(socket_path)`: opens a short-lived Unix-socket connection per command,
  writes one JSON line, and reads newline-delimited replies **skipping async `event` lines** until the
  one matching `request_id`. Raises `MpvNotRunning` (socket dead) / `MpvError` (mpv said not-success).
  `adjust_volume` clamps to 0–130. Pure I/O with an injectable path → testable against a fake server.
- `src/playlists.py` — `discover()` (case-insensitive **stable** sort so global indices stay valid
  between a `/mpv_list` render and a later `/mpv_play <n>`; scans `*.m3u` directly in a playlists dir
  **and one level of nested folders**, whose name becomes the playlist's `subcategory`), `find()`
  (numeric index OR case-insensitive substring), `validate()`/`missing_entries()` (the on-disk
  checker behind `/mpv_doctor`; resolves relative entries against the playlist's own dir, treats URLs
  as always-present), and `prettify()` (strips release/quality/source junk for **display only** —
  `Playlist.display` / button text; the raw `name` is what matching, callbacks and files use).
- `src/player.py` — the only part that spawns a process. `build_launch_command()` /
  `build_pipe_commands()` are pure helpers (use `MPV_RUNNER` if it exists, else plain `mpv`);
  `play_url()` streams a URL as `yt-dlp -o - | mpv -` (bare-URL messages and `/mpv_url` route here,
  gated by the anchored `_URL_RE`). **yt-dlp must do the fetching itself** — handing mpv resolved
  stream URLs or using mpv's ytdl hook breaks on IP-locked/client-bound CDN URLs (googlevideo) and
  on split-brain proxy egress; the pipe is the one shape that matches a plain download. The venv's
  yt-dlp (pip-installed nightly) is preferred over the system one; title arrives via
  `--print-to-file` during the same invocation; yt-dlp/mpv output → `/tmp/tg-mpv-bot-{ytdl,mpv}.log`.
  `play()` does `pkill -x mpv`,
  runs the optional `PRE_PLAY_HOOK` shell command, then a detached
  `Popen(..., start_new_session=True)` — the Python-native equivalent of the old `setsid` detachment
  for non-TTY contexts (asyncio/systemd) — then `POST_PLAY_HOOK`. Hooks get `PLAYLIST` /
  `PLAYLIST_NAME` / `MPV_SOCKET` / `DISPLAY` in their env; failures are logged, never fatal (15s
  timeout). **Never hardcode `I3SOCK`** — the i3 socket path embeds i3's PID and goes stale on every
  reboot (the old built-in switch broke exactly this way); `i3-msg` in a hook finds the socket via X11.
- `src/keyboards.py` — inline-keyboard builders for `/mpv_list`: **category → (subcategory) →
  paginated playlist buttons** (`PER_PAGE=8`). Flat categories (no subcategories) jump straight to the
  playlist list; categories with subcategories (tutorials → provider) show a subcategory menu first.
  Pure helpers (`categories`/`subcategories`/`indices_for`/`page_*`) are unit-tested. **Index-based**
  callback grammar (stable for a fixed library): `cats`, `c:<ci>[:<page>]`, `s:<ci>:<si>[:<page>]`,
  `pl:<global_index>`, `noop`.
- `src/commands.py` — handlers push blocking IPC/subprocess work to `asyncio.to_thread`. `_ipc()`
  centralizes error→message translation. Play buttons carry the **global** playlist index. `/mpv_info`
  posts the **now-playing panel** (`now_playing_keyboard` + `_status_text`); the `ctl:<action>`
  callbacks (`cb_ctl`, dispatched via `_CTL_ACTIONS`) run an IPC op then edit the panel in place.
  `/mpv_fix` calls `generate.repair_playlists`. The playlist scan is cached (`_all_playlists`,
  `refresh_cache`).
- `src/generate.py` — idempotent playlist creation (`generate_missing` → `generate_flat`/
  `generate_nested`, never overwrites, skips items already covered) behind `/mpv_scan`; plus
  `repair_playlists` (re-point missing entries by unique basename, prune the rest, `.m3u.bak` backup)
  behind `/mpv_fix`.
- `src/lock.py` — `acquire(path)` exclusive `flock`; raises `AlreadyRunning` if a second instance starts.

## Running it (operational)

- **Exactly one instance may poll.** Telegram allows a single long-poll per token; a second
  `python bot.py` makes both fight with `TelegramConflictError: terminated by other getUpdates request`,
  and taps land on whichever instance wins — often presenting as a "hang". Run via
  `systemctl --user {restart,status} tg-mpv-bot` **only**; never also launch it by hand. To check for
  strays: `pgrep -af bot.py`.
- **Reading logs:** `journalctl --user -u tg-mpv-bot` shows nothing on this host; use
  `journalctl --user-unit tg-mpv-bot` (or `journalctl _PID=$(systemctl --user show tg-mpv-bot -p MainPID --value)`).
- The media library is on a spinning external disk (`/mnt/EHDDSG-4`); `commands._all_playlists()` caches
  `discover()` and only re-scans on `/mpv_list` `/mpv_play` `/mpv_doctor` (not per button tap).

## Things to know before editing

- Adding a command touches **four** in-sync places: a handler in `src/commands.py`, the menu in
  `bot.py:_build_menu()`, the help text in `cmd_help`, and the README command table.
- The now-playing panel's `ctl:<action>` callbacks are one more callback grammar alongside
  `cats`/`c:`/`s:`/`pl:`/`noop` and the episode picker's `ep:<n>`/`eps:<page>`; `_CTL_ACTIONS` maps
  each panel action to an `MpvClient` call. (`ep:` indexes mpv's *live* playlist, not the library.)
- All host assumptions now flow from `src/config.py` env vars. The deploy files (`docker-compose.yml`,
  `tg-mpv-bot.service`) still set concrete values (`PRE_PLAY_HOOK=i3-msg workspace 10`, `DISPLAY=:0`,
  `%h`/`${HOME}`-relative paths)
  and must agree with them.
- `mpvctl.sh` is now **standalone/legacy** — kept for Hermes/CLI use (`./mpvctl.sh list|play|info`) but
  the bot no longer invokes it. It still hardcodes its own paths; it is *not* driven by `config.py`.
- The bot does not start mpv itself except via `src/player.play()`. IPC commands surface
  "mpv is not running" when the socket is dead.
</content>
</invoke>
