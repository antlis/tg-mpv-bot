from src.recorder import RECORD_MAX, build_record_args


def _val(args, flag):
    return args[args.index(flag) + 1]


def test_local_h264_video_copies_seeks_and_paces():
    a = build_record_args("/m/ep.mp4", pos=100, dur=600, is_video=True, vfmt="h264", out="/tmp/o.mp4", secs=30)
    assert _val(a, "-ss") == "100"
    assert "-re" in a
    assert _val(a, "-c:v") == "copy"
    assert _val(a, "-c:a") == "aac"
    assert _val(a, "-t") == "30"
    assert a[-1] == "/tmp/o.mp4"


def test_hevc_video_reencodes_to_h264_720p():
    a = build_record_args("/m/ep.mkv", pos=10, dur=600, is_video=True, vfmt="hevc", out="/tmp/o.mp4")
    assert "libx264" in a
    assert "scale=-2:720" in a
    assert "copy" not in a


def test_seek_is_clamped_inside_the_file():
    a = build_record_args("/m/ep.mp4", pos=9999, dur=600, is_video=True, vfmt="h264", out="/tmp/o.mp4")
    assert _val(a, "-ss") == "598"  # dur - 2


def test_http_radio_is_audio_opus_with_no_seek():
    a = build_record_args("http://stream/radio", pos=0, dur=0, is_video=False, vfmt="", out="/tmp/o.ogg")
    assert "-ss" not in a and "-re" not in a
    assert "-vn" in a and "libopus" in a


def test_duration_capped_at_one_hour():
    a = build_record_args("/m/ep.mp4", pos=0, dur=600, is_video=True, vfmt="h264", out="/tmp/o.mp4", secs=999999)
    assert _val(a, "-t") == str(RECORD_MAX)
