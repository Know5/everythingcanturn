"""CLI 集成测试：直接调用 main(argv)，不走子进程。

这些用例覆盖命令分发、参数解析和真实转换路径——CLI 是打包后用户唯一
能看到的诊断入口，0 覆盖会让 `convcore doctor` 这类命令的回归无人发现。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from convcore.cli import main


def _png(tmp_path: Path) -> Path:
    from PIL import Image

    p = tmp_path / "input.png"
    Image.new("RGB", (24, 16), (10, 120, 200)).save(p)
    return p


def test_presets_lists_names(capsys):
    assert main(["presets"]) == 0
    out = capsys.readouterr().out
    assert "original" in out and "web" in out


def test_formats_lists_domains(capsys):
    assert main(["formats"]) == 0
    out = capsys.readouterr().out
    assert "raster" in out and "PNG" in out


def test_formats_available_hides_gated(capsys):
    assert main(["formats", "--available"]) == 0
    assert "raster" in capsys.readouterr().out


def test_targets_for_png(capsys, tmp_path):
    png = _png(tmp_path)
    assert main(["targets", str(png)]) == 0
    out = capsys.readouterr().out
    assert "JPEG" in out or "jpeg" in out


def test_path_prints_plan(capsys, tmp_path):
    png = _png(tmp_path)
    assert main(["path", str(png), "webp"]) == 0
    out = capsys.readouterr().out
    assert "PNG" in out and "WebP" in out
    assert "保真度损失" in out


def test_info_prints_file_details(capsys, tmp_path):
    png = _png(tmp_path)
    assert main(["info", str(png)]) == 0
    out = capsys.readouterr().out
    assert "PNG" in out and "24" in out and "16" in out


def test_doctor_reports_plugins(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "插件" in out and "ffmpeg" in out.lower()


def test_engines_list(capsys):
    assert main(["engines", "list"]) == 0
    assert "ffmpeg" in capsys.readouterr().out.lower()


def test_convert_single_file(capsys, tmp_path):
    png = _png(tmp_path)
    out = tmp_path / "out.webp"
    assert main(["convert", str(png), "-t", "webp", "-o", str(out), "--overwrite"]) == 0
    assert out.is_file()
    assert "input.png" in capsys.readouterr().out


def test_convert_missing_input_returns_2(capsys, tmp_path):
    assert main(["convert", str(tmp_path / "nope.png"), "-t", "webp"]) == 2
    assert "文件不存在" in capsys.readouterr().err


def test_convert_unknown_target_returns_1(capsys, tmp_path):
    png = _png(tmp_path)
    assert main(["convert", str(png), "-t", "no/such", "-d", str(tmp_path)]) == 1
    assert "未知目标" in capsys.readouterr().err


def test_path_unknown_target_returns_2(capsys, tmp_path):
    png = _png(tmp_path)
    assert main(["path", str(png), "no/such"]) == 2
    assert "无法解析目标格式" in capsys.readouterr().err


def test_info_missing_file_returns_2(capsys, tmp_path):
    assert main(["info", str(tmp_path / "nope.png")]) == 2
    assert "文件不存在" in capsys.readouterr().err


def test_run_python_escape_hatch():
    """冻结环境里文件对话框靠它把自己的 exe 当 python 用。"""
    assert main(["--run-python", "x = 1 + 1"]) == 0
