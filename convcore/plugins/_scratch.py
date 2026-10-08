"""插件工作目录。

pipeline._run 会把本次转换的临时工作区通过 `_tmp` 注入每个插件的 opts，
产物写完再由 pipeline._detach 搬去最终位置。但插件也能被直接调用——库用法、
调试脚本、测试——这些调用常常不带 `_tmp`。

历史实现是 `opts.get("_tmp") or "."`，于是 built.pdf / out.pdf / table.pdf /
images.pdf 这类产物直接落在当前目录：一次手动调试就能把四个 PDF 漏进仓库根。

统一走 workdir()：没有 `_tmp` 时退化为进程级临时目录（退出时清理）。
CWD 从此不可能是工作区。
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping

_scratch: Path | None = None


def workdir(opts: Mapping[str, Any]) -> Path:
    """插件工作目录：优先 pipeline 注入的 `_tmp`，否则进程级临时目录。

    临时目录惰性创建：只跑正常管线的进程（_tmp 总是存在）不会多出一个空目录。
    """
    tmp = opts.get("_tmp")
    if tmp:
        return Path(tmp)
    global _scratch
    if _scratch is None or not _scratch.is_dir():
        _scratch = Path(tempfile.mkdtemp(prefix="convcore-scratch-"))
        atexit.register(shutil.rmtree, _scratch, ignore_errors=True)
    return _scratch
