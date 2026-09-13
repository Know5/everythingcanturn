"""外部引擎的探测与调用封装。

Phase 1 只需要 probe（doctor 命令要报告缺什么）。Phase 2 起加 ffmpeg/soffice
等具体封装，届时共用 base.py 的子进程安全调用逻辑。
"""

from .base import RunResult, run, stream_lines
from .probe import ENGINES, EngineInfo, engines_dir, probe_all, probe_one, require

__all__ = [
    "ENGINES",
    "EngineInfo",
    "RunResult",
    "engines_dir",
    "probe_all",
    "probe_one",
    "require",
    "run",
    "stream_lines",
]
