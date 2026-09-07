"""任务队列：批量转换、逐项进度、取消。

阶段二 Tauri 外壳会通过这层驱动 UI。现在做成同步 + 线程池，接口上预留取消，
因为 UI 的「取消」必须能中断已经在跑的任务，事后补这个语义会很痛。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .pipeline import Converter, convert_file
from .types import Cancelled, ConversionError, ConvertResult


class JobState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class Job:
    """一个转换任务。"""

    source: Path
    target: str
    out_dir: Path | None = None
    preset: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    overwrite: bool = False

    stem: str | None = None
    """产物文件名主干。队列在发现重名时填入消歧后的值。"""

    state: JobState = JobState.PENDING
    progress: float = 0.0
    step: str = ""
    result: ConvertResult | None = None
    error: str | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()


JobListener = Callable[[Job], None]


class JobQueue:
    """批量任务队列。

    并发数默认取 CPU 数但上限 4：转换是 CPU 和 IO 双重密集，开太多反而互相拖慢，
    而且 LibreOffice 这类引擎并发本身就脆弱。
    """

    def __init__(
        self,
        *,
        workers: int | None = None,
        converter: Converter | None = None,
        listener: JobListener | None = None,
    ) -> None:
        import os

        self.workers = workers or min(4, os.cpu_count() or 2)
        self.converter = converter or Converter()
        self.listener = listener
        self._jobs: list[Job] = []

    # 终态任务保留上限。CLI 单次进程本来活不长，但 JobQueue 也被
    # 长驻服务复用，同样要防只增不减。
    _KEEP_DONE = 500

    def add(self, job: Job) -> Job:
        self._prune()
        self._jobs.append(job)
        return job

    def _prune(self) -> None:
        """清扫终态任务，保留最近 _KEEP_DONE 条。"""
        done_idx = [
            i for i, j in enumerate(self._jobs)
            if j.state in (JobState.DONE, JobState.FAILED, JobState.CANCELLED)
        ]
        excess = len(done_idx) - self._KEEP_DONE
        if excess <= 0:
            return
        # 从后往前删，索引不漂移；只删最旧的 excess 个终态条目
        for i in reversed(done_idx[:excess]):
            del self._jobs[i]

    def add_files(
        self,
        sources: list[Path],
        target: str,
        *,
        out_dir: Path | None = None,
        preset: str | None = None,
        overwrite: bool = False,
    ) -> list[Job]:
        return [
            self.add(
                Job(
                    source=s,
                    target=target,
                    out_dir=out_dir,
                    preset=preset,
                    overwrite=overwrite,
                )
            )
            for s in sources
        ]

    @property
    def jobs(self) -> list[Job]:
        return list(self._jobs)

    def _notify(self, job: Job) -> None:
        if self.listener:
            self.listener(job)

    def _run_one(self, job: Job) -> None:
        if job.cancelled:
            job.state = JobState.CANCELLED
            self._notify(job)
            return

        job.state = JobState.RUNNING
        self._notify(job)

        def on_progress(frac: float, step: str) -> None:
            # 取消检查放在进度回调里：纯 Python 插件靠这个中断
            if job.cancelled:
                raise Cancelled("已取消")
            job.progress, job.step = frac, step
            self._notify(job)

        # 外部引擎（ffmpeg）跑在子进程里，进度回调之间可能隔很久，必须给它
        # 一个能主动查询的取消钩子，否则取消要等到当前子进程跑完才生效。
        overrides = {**job.overrides, "_cancel": lambda: job.cancelled}

        try:
            job.result = convert_file(
                job.source,
                job.target,
                out_dir=job.out_dir,
                preset=job.preset,
                overrides=overrides,
                overwrite=job.overwrite,
                progress=on_progress,
                # Converter/插件实例可能携带本次转换状态；并发任务不共享实例。
                converter=Converter(),
                stem=job.stem,
            )
            if job.cancelled:
                raise Cancelled("已取消")
            job.state = JobState.DONE
            job.progress = 1.0
        except Cancelled:
            job.state = JobState.CANCELLED
        except Exception as exc:
            job.state = JobState.FAILED
            job.error = str(exc)
        finally:
            self._notify(job)

    def _resolve_name_collisions(self, jobs: list[Job]) -> None:
        """同目录下产物重名时消歧。

        转换一个混合格式的文件夹时，photo.png 和 photo.bmp 都转 webp 会撞成
        同一个 photo.webp，后者静默覆盖前者。这是真实场景下的数据丢失，
        所以按源扩展名区分：photo.webp / photo-bmp.webp。
        """
        groups: dict[tuple[Path | None, str, str], list[Job]] = {}
        for j in jobs:
            key = (j.out_dir, j.target, j.source.stem)
            groups.setdefault(key, []).append(j)

        for (_, _, stem), group in groups.items():
            if len(group) < 2:
                continue
            # 按用户给出的顺序，第一个保留原名，其余带源格式后缀
            for i, j in enumerate(group):
                if i == 0:
                    continue
                suffix = j.source.suffix.lstrip(".").lower() or f"{i}"
                j.stem = f"{stem}-{suffix}"

    def run(self) -> list[Job]:
        """跑完所有任务。单任务时不起线程池，避免无谓开销。"""
        pending = [j for j in self._jobs if j.state is JobState.PENDING]
        if not pending:
            return self._jobs

        self._resolve_name_collisions(pending)
        if len(pending) == 1:
            self._run_one(pending[0])
            return self._jobs

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures: list[Future[None]] = [
                pool.submit(self._run_one, j) for j in pending
            ]
            for f in futures:
                f.result()
        return self._jobs

    def cancel_all(self) -> None:
        for j in self._jobs:
            if j.state in (JobState.PENDING, JobState.RUNNING):
                j.cancel()

    def summary(self) -> dict[JobState, int]:
        out: dict[JobState, int] = {}
        for j in self._jobs:
            out[j.state] = out.get(j.state, 0) + 1
        return out

    def __iter__(self) -> Iterator[Job]:
        return iter(self._jobs)
