#!/bin/bash
# mpvctl.sh — Zero-token mpv control via IPC socket
# Usage: mpvctl.sh <command> [args...]
# Designed to be called from Hermes quick_commands (no LLM)
# MUST use /bin/bash for proper nohup/disown handling in non-TTY contexts

set -o pipefail

SOCKET="/tmp/mpv-socket"
PLAYLIST_DIRS=(
    "$HOME/Videos/cartoons/playlists"
    "$HOME/Videos/movie/playlists"
    "$HOME/Videos/shows/playlists"
)
I3_SOCKET="/run/user/1000/i3/ipc-socket.2012"

# --- Build flat sorted list of all playlists ---
# Populates GLOBAL_PLAYLISTS array with entries "label|name|path"
_build_playlist_list() {
    GLOBAL_PLAYLISTS=()
    local tmpfile
    tmpfile=$(mktemp)
    for dir in "${PLAYLIST_DIRS[@]}"; do
        [ -d "$dir" ] || continue
        local label="${dir#$HOME/Videos/}"
        label="${label%/playlists}"
        for f in "$dir"/*.m3u; do
            [ -f "$f" ] || continue
            local name="${f##*/}"
            name="${name%.m3u}"
            echo "$name|$label|$f" >> "$tmpfile"
        done
    done
    # Sort by name (field before first |)
    while IFS='|' read -r name label path; do
        GLOBAL_PLAYLISTS+=("$name|$label|$path")
    done < <(sort -t'|' -k1 "$tmpfile")
    rm -f "$tmpfile"
}

# --- IPC helper ---
_send() {
    local cmd="$1"
    python3 -c "
import socket, json, sys
s = socket.socket(socket.AF_UNIX)
try:
    s.connect('$SOCKET')
except Exception:
    print('ERROR: mpv not running')
    sys.exit(2)
s.send(b'$cmd\n')
try:
    resp = s.recv(8192)
    print(json.loads(resp.decode().strip()).get('data', resp.decode().strip()))
except:
    pass
s.close()
"
}

# --- Volume helper (get + set) ---
_adjust_volume() {
    local delta="$1"
    python3 -c "
import socket, json
s = socket.socket(socket.AF_UNIX)
try: s.connect('$SOCKET')
except: print('ERROR: mpv not running'); exit(2)
# Get current volume
s.send(b'{\"command\":[\"get_property\",\"volume\"]}\n')
resp = json.loads(s.recv(8192).decode().strip())
vol = resp.get('data', 50)
new_vol = max(0, min(130, vol + $delta))
# Set new volume
s.send(b'{\"command\":[\"set_property\",\"volume\",' + str(new_vol).encode() + b']}\n')
s.close()
print(f'{new_vol}')
"
}

# --- Main dispatch ---
CMD="${1:-pause}"
shift 2>/dev/null || true

case "$CMD" in
    pause)
        _send '{"command":["set_property","pause",true]}'
        echo "⏸ Paused"
        ;;
    unpause|resume)
        _send '{"command":["set_property","pause",false]}'
        echo "▶ Resumed"
        ;;
    quit|stop)
        _send '{"command":["quit"]}'
        echo "⏹ Quit"
        ;;
    volup)
        new_vol=$(_adjust_volume 10)
        [ $? -eq 0 ] && echo "🔊 Volume: ${new_vol}" || echo "$new_vol"
        ;;
    voldown)
        new_vol=$(_adjust_volume -10)
        [ $? -eq 0 ] && echo "🔉 Volume: ${new_vol}" || echo "$new_vol"
        ;;
    mute)
        _send '{"command":["cycle","mute"]}'
        echo "🔇 Mute toggled"
        ;;
    info|status)
        echo "📺 mpv status:"
        _send '{"command":["get_property","path"]}' 2>/dev/null || echo "    Not playing"
        _send '{"command":["get_property","time-pos"]}' 2>/dev/null || true
        _send '{"command":["get_property","volume"]}' 2>/dev/null || true
        ;;
    list)
        _build_playlist_list
        if [ ${#GLOBAL_PLAYLISTS[@]} -eq 0 ]; then
            echo "❌ No playlists found"
            exit 1
        fi
        echo "📋 Playlists:"
        i=1
        for entry in "${GLOBAL_PLAYLISTS[@]}"; do
            name="${entry%%|*}"
            rest="${entry#*|}"
            label="${rest%%|*}"
            printf "  %2d. %s\n" "$i" "$name"
            i=$((i + 1))
        done
        echo ""
        echo "Use /mpv_play <number> to play"
        ;;
    play)
        SEARCH="${1:-}"
        if [ -z "$SEARCH" ]; then
            echo "Usage: play <number-or-name>"
            echo "Use /mpv_list to see available playlists"
            exit 1
        fi

        _build_playlist_list
        if [ ${#GLOBAL_PLAYLISTS[@]} -eq 0 ]; then
            echo "❌ No playlists found"
            exit 1
        fi

        MATCH=""
        # If arg is a number, use it as 1-based index
        if [[ "$SEARCH" =~ ^[0-9]+$ ]]; then
            idx=$((SEARCH - 1))
            if [ "$idx" -ge 0 ] && [ "$idx" -lt ${#GLOBAL_PLAYLISTS[@]} ]; then
                entry="${GLOBAL_PLAYLISTS[$idx]}"
                path="${entry##*|}"
                MATCH="$path"
                playlist_name="${entry%%|*}"
            else
                echo "❌ Invalid number: $SEARCH (1-${#GLOBAL_PLAYLISTS[@]})"
                exit 1
            fi
        else
            # Search by name (case-insensitive)
            for entry in "${GLOBAL_PLAYLISTS[@]}"; do
                name="${entry%%|*}"
                rest="${entry#*|}"
                label="${rest%%|*}"
                path="${rest#*|}"
                if echo "$name" | grep -iq "$SEARCH"; then
                    MATCH="$path"
                    playlist_name="$name"
                    break
                fi
            done
        fi

        if [ -z "$MATCH" ]; then
            echo "❌ No playlist matching '$SEARCH'"
            echo "Try /mpv_list"
            exit 1
        fi

        # Detach mpv from the shell's job table so the script exits immediately.
        # Use setsid (not nohup) — setsid creates a new session and fully detaches
        # from the controlling terminal, which works correctly in non-TTY contexts
        # (asyncio subprocess, systemd, tmux). nohup+disown can fail when there's
        # no TTY to detach from, causing SIGTERM on script exit.
        # Kill old mpv, switch workspace, then launch via setsid.
        pkill mpv 2>/dev/null || true
        sleep 0.3
        if [ -e "$I3_SOCKET" ]; then
            I3SOCK="$I3_SOCKET" i3-msg workspace 10 >/dev/null 2>&1 || true
        fi
        echo "▶ Playing: $playlist_name"
        setsid /tmp/mpv-runner.sh --playlist="$MATCH" --input-ipc-server="$SOCKET" --force-window </dev/null >/dev/null 2>&1 &
        ;;

    # -- Seek commands --
    fwd|forward)
        _send '{"command":["seek",30]}'
        echo "⏩ +30s"
        ;;
    back|rewind)
        _send '{"command":["seek",-10]}'
        echo "⏪ -10s"
        ;;
    *)
        echo "mpvctl — mpv remote control"
        echo ""
        echo "Commands:"
        echo "  pause              Pause playback"
        echo "  unpause/resume     Resume playback"
        echo "  quit/stop          Quit mpv"
        echo "  volup              Volume +10"
        echo "  voldown            Volume -10"
        echo "  mute               Toggle mute"
        echo "  info/status        Show current playback info"
        echo "  list               List playlists"
        echo "  play <name>        Search and play a playlist"
        echo "  fwd/forward        Seek forward 30s"
        echo "  back/rewind        Seek backward 10s"
        exit 1
        ;;
esac
