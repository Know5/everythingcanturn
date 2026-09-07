"""冻结环境下的路径解析。

PyInstaller 打包后有两件事会变，而且都是静默失败——不报错，只是找不到东西：

1. `Path(__file__).parent` 指向临时解包目录（onefile 模式下是 %TEMP%\\_MEIxxxxx），
   而不是源码树。捆绑的静态资源要从 sys._MEIPASS 取。
2. `sys.executable` 是我们自己的 exe，不再是 python.exe。所以
   `[sys.executable, "-c", code]` 会把 "-c" 当成命令行参数喂给我们的 CLI 解析器。

两个函数都在未冻结时退回原行为，所以开发和打包用同一套代码。
"""

from __future__ import annotations

import sys
from pathlib import Path


def frozen() -> bool:
    """是否运行在 PyInstaller 打好的包里。"""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def resource(*parts: str) -> Path:
    """定位捆绑的只读资源。

    参数是相对包根（convcore/ 的父目录）的路径片段，例如
    resource("convcore", "webui", "static")。
    """
    if frozen():
        return Path(sys._MEIPASS).joinpath(*parts)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent.joinpath(*parts)


def ensure_streams() -> None:
    """保证 sys.stdout/stderr 可写。

    PyInstaller 的 windowed 模式（console=False）里两者都是 None，于是任何
    print 都抛 AttributeError。GUI 代码路径上散落着诊断输出和第三方库的
    警告，挨个改成条件判断不现实，直接换成黑洞。
    """
    import io

    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, io.StringIO())


def python_cmd(code: str) -> list[str]:
    """构造「用 Python 跑一段代码」的命令行。

    冻结后没有可用的 python.exe，改走一个约定的自举开关：exe 以
    `--run-python <code>` 启动时只执行这段代码然后退出（见 cli.py）。
    这样文件对话框那类必须独立进程的活在打包后依然可用。
    """
    if frozen():
        return [sys.executable, "--run-python", code]
    return [sys.executable, "-c", code]
