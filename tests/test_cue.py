"""CUE 分轨：索引解析、输出命名和时长。"""

from __future__ import annotations

import subprocess

import pytest

from convcore.cue import parse_cue, split_cue
from convcore.engines.probe import ENGINES, companion, probe_one

_spec = next(s for s in ENGINES if s.key == "ffmpeg")
_info = probe_one(_spec)

pytestmark = pytest.mark.skipif(
    not _info.available,
    reason="需要 ffmpeg：convcore engines install ffmpeg",
)


def test_cue_parse_and_split(tmp_path):
    assert _info.path is not None
    wav = tmp_path / "album.wav"
    subprocess.run(
        [
            str(_info.path), "-y", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=4", "-c:a", "pcm_s16le", str(wav),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    cue = tmp_path / "album.cue"
    cue.write_text(
        'PERFORMER "Artist"\n'
        'TITLE "Album"\n'
        'FILE "album.wav" WAVE\n'
        '  TRACK 01 AUDIO\n'
        '    TITLE "One"\n'
        '    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n'
        '    TITLE "Two"\n'
        '    INDEX 01 00:02:00\n',
        encoding="utf-8",
    )

    sheet = parse_cue(cue)
    assert sheet.title == "Album"
    assert [t.start for t in sheet.tracks] == [0.0, 2.0]

    out = split_cue(cue, "mp3", out_dir=tmp_path / "out", overwrite=True)
    assert [p.name for p in out] == ["01 - One.mp3", "02 - Two.mp3"]
    probe = companion("ffmpeg", "ffprobe")
    for path in out:
        duration = subprocess.run(
            [
                str(probe), "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(path),
            ],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout.strip()
        assert float(duration) == pytest.approx(2.0, abs=0.3)
