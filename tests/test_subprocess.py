"""子进程调用层测试。

用 Python 自己当被测「引擎」，这样不依赖任何外部程序也能验证超时、
错误捕获、参数安全传递这些性质。
"""

from __future__ import annotations

import sys

import pytest

from convcore.engines.base import EngineTimeout, RunResult, run, stream_lines
from convcore.types import ConversionError

PY = sys.executable


def test_captures_stdout():
    res = run([PY, "-c", "print('hello')"], engine_name="test")
    assert res.ok
    assert b"hello" in res.stdout


def test_nonzero_exit_raises_with_stderr():
    """错误信息必须包含引擎的真实输出，不能只说「失败」。"""
    with pytest.raises(ConversionError, match="boom"):
        run(
            [PY, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"],
            engine_name="test",
        )


def test_check_false_returns_result():
    res = run([PY, "-c", "import sys; sys.exit(7)"], check=False, engine_name="test")
    assert not res.ok
    assert res.returncode == 7


def test_timeout_kills_process():
    """损坏的输入能让解码器进死循环，超时是必须的。"""
    with pytest.raises(EngineTimeout, match="未完成"):
        run([PY, "-c", "import time; time.sleep(30)"], timeout=1.0, engine_name="test")


def test_missing_executable_reports_clearly():
    with pytest.raises(ConversionError, match="找不到可执行文件"):
        run(["definitely-not-a-real-program-xyz"], engine_name="test")


def test_arguments_are_not_shell_interpreted(tmp_path):
    """参数里的 shell 元字符必须原样传递，不能被解释执行。

    这是防命令注入的核心性质：文件名叫 "a; rm -rf b" 也不该出事。
    """
    marker = tmp_path / "should-not-exist.txt"
    nasty = f'x"; python -c "open(r\'{marker}\',\'w\')"; echo '
    res = run([PY, "-c", "import sys; print(sys.argv[1])", nasty], engine_name="test")
    assert nasty.encode() in res.stdout.replace(b"\r\n", b"\n")
    assert not marker.exists(), "shell 元字符被执行了"


def test_stdout_limit_truncates():
    res = run(
        [PY, "-c", "print('x' * 100000)"], stdout_limit=1000, engine_name="test"
    )
    assert len(res.stdout) == 1000


def test_error_summary_filters_noise():
    """ffmpeg 会先刷一大段版本配置信息，真正的错误在最后。"""
    noisy = (
        "ffmpeg version 9.0.1 Copyright (c) 2000-2026\n"
        "  built with gcc 14\n"
        "  configuration: --enable-gpl --enable-libx264\n"
        "  libavutil 60.1.100\n"
        "Invalid data found when processing input\n"
    )
    res = RunResult(1, b"", noisy)
    summary = res.error_summary()
    assert "Invalid data found" in summary
    assert "configuration:" not in summary


def test_stream_lines_yields_progressively():
    lines = list(
        stream_lines(
            [PY, "-c", "for i in range(5): print(f'k={i}')"], engine_name="test"
        )
    )
    assert lines == [f"k={i}" for i in range(5)]


def test_stream_lines_raises_on_failure():
    with pytest.raises(ConversionError, match="bad input"):
        list(
            stream_lines(
                [
                    PY,
                    "-c",
                    "import sys; print('k=1'); sys.stderr.write('bad input\\n'); sys.exit(2)",
                ],
                engine_name="test",
            )
        )


def test_stream_lines_survives_large_stderr():
    """stderr 必须并发读取。不然管道缓冲区填满后子进程会阻塞在写 stderr 上，
    形成死锁——子进程编程最经典的坑。这个测试会挂住而非失败，所以有超时。
    """
    code = (
        "import sys\n"
        "for i in range(2000): sys.stderr.write('noise line %d\\n' % i)\n"
        "for i in range(3): print('k=%d' % i)\n"
    )
    lines = list(stream_lines([PY, "-c", code], timeout=30, engine_name="test"))
    assert lines == ["k=0", "k=1", "k=2"]


def test_stream_lines_cancel_stops_early():
    code = "import time\nfor i in range(1000):\n    print('k=%d' % i, flush=True)\n    time.sleep(0.01)\n"
    seen: list[str] = []
    for line in stream_lines(
        [PY, "-c", code], timeout=30, engine_name="test", cancel=lambda: len(seen) >= 3
    ):
        seen.append(line)
    # 取消后不抛错（用户主动取消不是失败），且没跑完全部 1000 行
    assert 3 <= len(seen) < 100


def test_stream_lines_cancel_without_stdout():
    """无进度输出的引擎也必须能及时响应取消。"""
    code = "import time; time.sleep(30)"
    lines = list(
        stream_lines(
            [PY, "-c", code], timeout=30, engine_name="test", cancel=lambda: True
        )
    )
    assert lines == []


def test_stream_lines_consumer_abort_kills_process():
    """上层回调异常关闭迭代器时，不能留下后台引擎。"""
    import time

    stream = stream_lines(
        [
            PY,
            "-c",
            "print('ready', flush=True); import time; time.sleep(30)",
        ],
        timeout=30,
        engine_name="test",
    )
    assert next(stream) == "ready"
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="stop"):
        stream.throw(RuntimeError("stop"))
    assert time.monotonic() - started < 2
