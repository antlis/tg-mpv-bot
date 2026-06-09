# Changelog

Notable changes to **tg-mpv-bot**. Format based on
[Keep a Changelog](https://keepachangelog.com/).

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

[1.3.0]: https://github.com/antlis/tg-mpv-bot/releases/tag/v1.3.0
