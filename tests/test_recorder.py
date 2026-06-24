from src.recorder import RECORD_MAX, build_record_args, parse_time


def _val(args, flag):
    return args[args.index(flag) + 1]


def test_local_h264_video_reencodes_seeks_and_paces():
    a = build_record_args(
        "/m/ep.mp4", pos=100, dur=600, is_video=True, vfmt="h264", out="/tmp/o.mp4", secs=30
    )
    assert _val(a, "-ss") == "100"
    assert "-re" in a
    assert _val(a, "-c:v") == "libx264"
    assert "scale=-2:720" in a
    assert _val(a, "-c:a") == "aac"
    assert _val(a, "-t") == "30"
    assert a[-1] == "/tmp/o.mp4"


def test_hevc_video_reencodes_to_h264_720p():
    a = build_record_args(
        "/m/ep.mkv", pos=10, dur=600, is_video=True, vfmt="hevc", out="/tmp/o.mp4"
    )
    assert "libx264" in a
    assert "scale=-2:720" in a
    assert "copy" not in a
    # A/V sync: no B-frames (so video starts at PTS 0) + audio resample
    assert a[a.index("-bf") + 1] == "0"
    assert any("aresample" in x for x in a)


def test_seek_is_clamped_inside_the_file():
    a = build_record_args(
        "/m/ep.mp4", pos=9999, dur=600, is_video=True, vfmt="h264", out="/tmp/o.mp4"
    )
    assert _val(a, "-ss") == "598"  # dur - 2


def test_http_radio_is_audio_opus_with_no_seek():
    a = build_record_args(
        "http://stream/radio", pos=0, dur=0, is_video=False, vfmt="", out="/tmp/o.ogg"
    )
    assert "-ss" not in a and "-re" not in a
    assert "-vn" in a and "libopus" in a


def test_duration_capped_at_one_hour():
    a = build_record_args(
        "/m/ep.mp4", pos=0, dur=600, is_video=True, vfmt="h264", out="/tmp/o.mp4", secs=999999
    )
    assert _val(a, "-t") == str(RECORD_MAX)


# ── parse_time ───────────────────────────────────────────────────────


def test_parse_time_hms():
    assert parse_time("01:30:00") == 5400
    assert parse_time("00:05:30") == 330


def test_parse_time_ms():
    assert parse_time("5:30") == 330


def test_parse_time_suffix():
    assert parse_time("30m") == 1800
    assert parse_time("2h") == 7200
    assert parse_time("45s") == 45


def test_parse_time_plain_int():
    assert parse_time("120") == 120


def test_parse_time_invalid():
    assert parse_time("abc") is None
    assert parse_time("1:2:x") is None


# ── start_secs ───────────────────────────────────────────────────────


def test_start_secs_overrides_position():
    a = build_record_args(
        "/m/ep.mp4", pos=100, dur=7200, is_video=False, vfmt="", out="/tmp/o.ogg",
        secs=1800, start_secs=5400,
    )
    assert _val(a, "-ss") == "5400"
    assert _val(a, "-t") == "1800"


def test_start_secs_clamped_to_dur():
    a = build_record_args(
        "/m/ep.mp4", pos=0, dur=600, is_video=False, vfmt="", out="/tmp/o.ogg",
        start_secs=599,
    )
    assert _val(a, "-ss") == "598"  # min(599, 600 - 2)


def test_start_secs_zero_omits_ss_flag():
    a = build_record_args(
        "/m/ep.mp4", pos=50, dur=600, is_video=False, vfmt="", out="/tmp/o.ogg",
        start_secs=0,
    )
    assert "-ss" not in a


def test_start_secs_ignored_for_http():
    a = build_record_args(
        "http://stream/", pos=0, dur=0, is_video=False, vfmt="", out="/tmp/o.ogg",
        secs=300, start_secs=600,
    )
    assert "-ss" not in a
    assert "-re" not in a
