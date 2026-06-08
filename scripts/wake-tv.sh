#!/bin/sh
# PRE_PLAY_HOOK: wake the HDMI TV before mpv launches, then switch to the
# media workspace. Runs with DISPLAY/PATH from the bot's hook env.
#
# Why: when the TV is in DPMS sleep, launching mpv lands the window on the
# laptop instead of the TV. Manually nudging the pointer onto the TV (via
# x2x) wakes it and fixes the target — this reproduces that nudge, but only
# when the screen is actually asleep, so an already-on TV pays no latency.
#
# The mouse jiggle moves and immediately moves back: non-destructive, just
# enough input to register. The sleep covers HDMI link re-negotiation, which
# otherwise loses the race with mpv's window mapping.

case "$(xset -q 2>/dev/null | grep -oE 'Monitor is (On|Off|in Standby|in Suspend)')" in
  "Monitor is On" | "")
    : ;;  # awake (or DPMS unavailable) — nothing to wake
  *)
    xset dpms force on
    xdotool mousemove_relative -- 3 3
    xdotool mousemove_relative -- -3 -3
    sleep 1.5  # let the HDMI link come back before mpv maps its window
    ;;
esac

exec i3-msg workspace 10
