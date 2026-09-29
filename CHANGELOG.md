# Changelog

Notable changes to **tg-mpv-bot**. Format based on
[Keep a Changelog](https://keepachangelog.com/).

## [1.9.0] — 2026-09-30
### Changed
- **Commands lost the `mpv_` prefix** (breaking — old names are gone): `/mpv_list` → **`/library`**, `/mpv_play` → `/play`, `/mpv_info` → `/info`, `/mpv_doctor` → `/doctor`, and so on for every command. `/list` specifically became `/library`. Secondary aliases kept their short forms (`/goto`+`/seek`, `/rec`, `/ep`, `/recent`, …) and the bare `/mpv <query>` still works as a `/play` alias. Telegram menu, `/help`, README, docs and CLAUDE.md were updated in the same pass.

### Fixed
- **Commands periodically did nothing — you had to run them twice** (the `/library` symptom). A transient `TelegramNetworkError: Cannot connect to host api.telegram.org:443` killed the outgoing reply while the handler itself succeeded, and the global error handler only logged it. The bot now uses `ResilientSession`, which retries connect-phase failures 3× with backoff — safe to retry because those fail before a single byte reaches Telegram, so no message can be duplicated. Timeouts and mid-request disconnects are still re-raised untouched.

## [1.8.0] — 2026-09-30
### Added
- **The transport panel now rides on every "playback started" message**, not just `/mpv_info` — playlist plays, random pick, history replay, URL/stream start, radio tuning, uploaded Telegram media, the IPTV "Now streaming" card and the "⏭ Now playing" notification all carry the buttons, so playback is one tap away from wherever you started it (same behaviour as WarMusicBot's `control_panel`).
- **📸 button on the panel** — screenshot the current frame into the chat (the `/mpv_shot` command still works as before).
- **🗑 button on the panel** — delete the now-playing message, for chats where the buttons pile up.
- **🔈 unmute button** — mute is now 🔇 mute / 🔈 unmute instead of a single blind toggle, so the two states are always separately reachable. `MpvClient.set_mute()` backs it.

### Changed
- **Panel layout** — volume moved to its own row (🔉 🔇 🔈 🔊), track/shuffle/loop/refresh share another (💬 🎧 🔀 🔁 🔄), and 📸 ⏺ ⏹ 🗑 form the last row. All previous buttons are still there.

## [1.7.0] — 2026-06-25
### Added
- **`/mpv_record START END`** — clip a specific time range from what's playing. Accepts `HH:MM:SS`, `MM:SS`, `Nh`/`Nm`/`Ns`, or plain seconds for both arguments (e.g. `/mpv_record 01:30:00 02:00:00` records 30 minutes starting at 1 h 30 m). Works for local files; for live HTTP streams (radio, IPTV) the start offset is ignored and only the duration (`END − START`) is used, with a note in the status message.
- **Duration shorthand** for `/mpv_record` — e.g. `/mpv_record 30m` or `/mpv_record 1h` alongside the existing plain-seconds form.
- **Ahead-of-playback warning** — when the requested start time is ahead of the current mpv position (meaning the file may not be downloaded that far yet), the recording starts but the status message warns that the clip may be shorter than expected.

## [1.6.0] — 2026-06-21
### Added
- **IPTV (`/mpv_iptv <name>`)** — search 50 000+ live TV channels from the [iptv-org](https://github.com/iptv-org/iptv) public catalogue and stream them live via mpv. Results shown as inline buttons; channel logo sent as a photo card when streaming starts. `/mpv_iptv` with no args shows links to browse channels by country/category.

## [1.5.0] — 2026-06-21
### Added
- **Watch history** (`/history`) — paginated list of recently played items
  (newest first, up to 20 entries, 8 per page). Each row shows a type icon
  (🔗 URL / 📁 local file), the title, and a 🗑 delete button. Tapping the
  icon sends the raw URL or file path so you can copy it; tapping the title
  replays the item; tapping 🗑 removes it from history and refreshes in place.
  History persists across restarts.
- **`/history` alias** — shorter alternative to `/mpv_history` / `/mpv_recent`.
- **`state.delete_history_entry`** — removes one entry from the JSON state file
  by target URL/path.

### Fixed
- **Stale history indices after replay** — tapping a history entry moved it to
  position 0 (newest), making subsequent taps hit the wrong item. The keyboard
  is now refreshed immediately after each replay so indices stay current.
- **HLS streams shown as static thumbnail** — `_is_audio_only` used
  `vcodec or "none"` which treated Python `None` (HLS manifests don't expose
  per-format codec info) the same as the explicit string `"none"`. Streams where
  both `vcodec` and `acodec` are `None`/absent are now correctly treated as
  video-bearing (HLS mux); audio-only detection fires only when `vcodec=="none"`
  or when `vcodec` is absent but `acodec` is present (SoundCloud pattern).

## [1.3.1] — 2026-06-09
### Fixed
- **Recording A/V sync** — recorded video clips had audio roughly 80 ms ahead of
  the picture. The re-encode now drops B-frames (`-bf 0`, so the first frame
  starts at PTS 0), forces constant frame rate, and resamples the audio to lock
  it to the video clock.

## [1.3.0] — 2026-06-09
### Added
- **`/mpv_record`** — record what's playing and send it to the chat: the current
  video as an H.264 mp4 (re-encoded only when the source isn't already H.264,
  e.g. HEVC), or radio/audio as an Opus voice message. Toggle it from the
  now-playing panel (**⏺ Rec** / **⏺ Stop**) or the command; an optional
  `[secs]` sets a fixed length, and it auto-stops at 1 hour. Local files seek to
  the live position and capture in realtime; live streams capture going forward.

## [1.1.0] and earlier
Library browsing, link/YouTube streaming, internet radio, file forwarding, the
now-playing transport panel (seek-to-%, volume, speed, tracks), watch history /
continue, screenshots, sleep timer, loudness normalization, and the
health/doctor/scan tooling.

[1.9.0]: https://github.com/antlis/tg-mpv-bot/releases/tag/v1.9.0
[1.3.1]: https://github.com/antlis/tg-mpv-bot/releases/tag/v1.3.1
[1.3.0]: https://github.com/antlis/tg-mpv-bot/releases/tag/v1.3.0
