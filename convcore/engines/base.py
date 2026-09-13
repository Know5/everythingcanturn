"""外部引擎的子进程调用层。

这一层的存在理由是安全和可诊断性。这些解析器历史上是 CVE 重灾区
（ImageMagick 尤其），而且失败信息全在 stderr 里——笼统报「转换失败」
对用户毫无帮助。

铁律：
- 参数永远用列表传，绝不 shell=True。文件名里的空格、引号、分号、中文
  都会在字符串拼接下出事。
- 必须有超时。损坏的输入能让解码器进死循环。
- 输出必须有上限。有些引擎在异常时会往 stderr 疯狂刷日志，无上限读取
  会把内存吃光。
- stderr 要留尾部。诊断信息通常在最后几行。
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue

from ..types import ConversionError

# stderr 保留的尾部字节数。够放下 ffmpeg 的错误说明，又不至于吃内存。
STDERR_TAIL = 64 * 1024

# 单次调用默认超时。转码大文件会很久，具体调用点应按输入大小放宽。
DEFAULT_TIMEOUT = 600.0

# Windows 上避免每次调用弹黑框。GUI 阶段这是必需的，CLI 阶段也更干净。
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


@dataclass(slots=True)
class RunResult:
    returncode: int
    stdout: bytes
    stderr: str
    """已解码的 stderr 尾部。引擎的错误信息不保证是 UTF-8，用 replace 兜底。"""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def error_summary(self, limit: int = 400) -> str:
        """从 stderr 里挑出最可能是原因的几行。

        ffmpeg 会先刷一大段版本和配置信息，真正的错误在最后。直接把整个
        stderr 抛给用户是噪音。
        """
        lines = [ln.strip() for ln in self.stderr.splitlines() if ln.strip()]
        # 过滤掉编译配置这类噪音行
        noise = ("configuration:", "built with", "libav", "  Metadata:", "Copyright (c)")
        signal = [ln for ln in lines if not ln.startswith(noise)]
        tail = signal[-4:] if signal else lines[-4:]
        out = " / ".join(tail)
        return out[:limit] if out else "引擎无错误输出"


class EngineTimeout(ConversionError):
    """引擎超时。多半是输入损坏导致解码器卡死。"""


def run(
    cmd: list[str | Path],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    stdout_limit: int = 8 * 1024 * 1024,
    check: bool = True,
    engine_name: str = "引擎",
) -> RunResult:
    """跑一个引擎命令并等它结束。

    stdout 有上限是因为 ffprobe 之类会输出 JSON，正常几十 KB，异常时可能爆量。
    """
    argv = [str(a) for a in cmd]
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,  # 显式写出来：这是安全边界，不是默认值可以依赖的事
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError as exc:
        raise ConversionError(f"找不到可执行文件：{argv[0]}") from exc
    except OSError as exc:
        raise ConversionError(f"无法启动 {argv[0]}：{exc}") from exc

    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise EngineTimeout(
            f"{engine_name} 超过 {timeout:.0f}s 未完成，已终止"
        ) from None

    result = RunResult(
        returncode=proc.returncode,
        stdout=out[:stdout_limit] if out else b"",
        stderr=(err[-STDERR_TAIL:] if err else b"").decode("utf-8", "replace"),
    )

    if check and not result.ok:
        raise ConversionError(
            f"{engine_name} 执行失败（退出码 {result.returncode}）：{result.error_summary()}"
        )
    return result


def stream_lines(
    cmd: list[str | Path],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    engine_name: str = "引擎",
    cancel: Callable[[], bool] | None = None,
) -> Iterator[str]:
    """跑命令并逐行产出 stdout，用于进度解析。

    结束时若退出码非零会抛错，错误信息取自 stderr——所以 stderr 必须并发
    读取，否则管道缓冲区填满后子进程会阻塞在写 stderr 上，形成死锁。
    这是子进程编程最经典的坑。
    """
    argv = [str(a) for a in cmd]
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=_NO_WINDOW,
            bufsize=1,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise ConversionError(f"找不到可执行文件：{argv[0]}") from exc

    err_chunks: list[str] = []

    def drain_stderr() -> None:
        assert proc.stderr is not None
        total = 0
        for line in proc.stderr:
            if total < STDERR_TAIL:
                err_chunks.append(line)
                total += len(line)
            elif len(err_chunks) > 200:
                # 只留尾部，丢掉中间，避免无界增长
                del err_chunks[: len(err_chunks) // 2]

    t = threading.Thread(target=drain_stderr, daemon=True)
    t.start()

    # Windows 的 selectors 不支持普通管道，因此 stdout 也由独立线程读入
    # 有界等待的队列。主线程可以在没有任何输出时仍然轮询取消和超时，
    # 不会被 readline 永久阻塞。
    end_of_stdout = object()
    stdout_queue: Queue[object] = Queue()

    def drain_stdout() -> None:
        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                stdout_queue.put(line)
        finally:
            stdout_queue.put(end_of_stdout)

    stdout_thread = threading.Thread(target=drain_stdout, daemon=True)
    stdout_thread.start()

    killed_by_cancel = False
    timed_out = False
    reached_eof = False
    deadline = time.monotonic() + timeout

    def kill_process() -> None:
        if proc.poll() is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    try:
        while True:
            if cancel is not None and cancel():
                killed_by_cancel = True
                kill_process()
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                kill_process()
                break

            try:
                line = stdout_queue.get(timeout=min(0.1, remaining))
            except Empty:
                continue

            if line is end_of_stdout:
                # A process can finish between the callback check and EOF. Give
                # an already-set cancellation flag precedence over a success.
                if cancel is not None and cancel():
                    killed_by_cancel = True
                reached_eof = True
                break
            yield str(line).rstrip("\r\n")
    finally:
        if proc.poll() is None:
            try:
                # Cancellation and generator close must never leave an orphaned
                # encoder behind. A normal process still gets the remaining
                # timeout to flush its output before we kill it.
                wait_for = 0.0 if killed_by_cancel or timed_out or not reached_eof else max(
                    0.0, deadline - time.monotonic()
                )
                proc.wait(timeout=wait_for)
            except subprocess.TimeoutExpired:
                kill_process()
                proc.wait()
        t.join(timeout=5)
        stdout_thread.join(timeout=5)

    if killed_by_cancel:
        # Keep the historical stream_lines contract (cancellation itself is not
        # an engine failure); callers that need an exception check the callback
        # after iteration. The media plugin does exactly that before returning.
        return

    if timed_out:
        raise EngineTimeout(
            f"{engine_name} 超过 {timeout:.0f}s 未完成，已终止"
        ) from None

    if proc.returncode != 0:
        stderr = "".join(err_chunks)[-STDERR_TAIL:]
        res = RunResult(proc.returncode, b"", stderr)
        raise ConversionError(
            f"{engine_name} 执行失败（退出码 {proc.returncode}）：{res.error_summary()}"
        )
