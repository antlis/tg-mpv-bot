#!/bin/sh
# Wrapper that adds --impersonate to the venv yt-dlp.
# mpv's ytdl_hook calls this instead of yt-dlp directly, bypassing the
# global ~/.config/yt-dlp/config which would break the system yt-dlp.
# The venv is resolved relative to this script so it works both on the host
# and inside the Docker image (/app).
here=$(dirname "$(readlink -f "$0")")
exec "$here/../.venv/bin/yt-dlp" --impersonate chrome "$@"
