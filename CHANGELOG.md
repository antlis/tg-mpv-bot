# Changelog

Notable changes to **tg-mpv-bot**. Format based on
[Keep a Changelog](https://keepachangelog.com/).

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

[1.3.1]: https://github.com/antlis/tg-mpv-bot/releases/tag/v1.3.1
[1.3.0]: https://github.com/antlis/tg-mpv-bot/releases/tag/v1.3.0
