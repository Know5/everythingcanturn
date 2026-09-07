"""PyInstaller 入口脚本。

不能直接拿 convcore/__main__.py 当入口：PyInstaller 把入口当顶层脚本执行，
__package__ 是空的，里面的 `from ._paths import ...` 会抛
"attempted relative import with no known parent package"。
这个文件在包外，用绝对导入，所以两种运行方式都成立。
"""

from convcore._paths import ensure_streams

# 必须在导入 cli 之前——cli 模块顶层就在 reconfigure 这两个流。
ensure_streams()

from convcore.cli import main  # noqa: E402

raise SystemExit(main())
