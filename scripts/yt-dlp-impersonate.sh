#!/bin/sh
# Wrapper that adds --impersonate to the venv yt-dlp.
# mpv's ytdl_hook calls this instead of yt-dlp directly, bypassing the
# global ~/.config/yt-dlp/config which would break the system yt-dlp.
exec /home/lad/Projects/tg-mpv-bot/.venv/bin/yt-dlp --impersonate chrome "$@"
