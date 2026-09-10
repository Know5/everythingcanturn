# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。构建：.venv/Scripts/python -m PyInstaller convcore.spec

三个要点：

1. 插件走 importlib.import_module 动态加载（plugins/__init__.py:_MODULES），
   PyInstaller 的静态分析看不见，必须在 hiddenimports 里逐个列出。漏一个的表现
   是「格式数量变少」而不是报错——load_all 会静默 continue。

2. 外部引擎（ffmpeg 196MB / soffice 350MB）不进包。它们装在
   %LOCALAPPDATA%\\convcore\\engines\\，由 engines install 按需下载。
   全捆进来 exe 会到 600MB，而多数人只转图片和文档。

3. onedir 而不是 onefile。onefile 每次启动都要把几百个文件解压到 %TEMP%，
   冷启动要 3-5 秒；onedir 大约 1 秒。桌面应用启动速度值这点目录整洁。
"""

from PyInstaller.utils.hooks import collect_submodules

# 动态导入的插件模块。与 plugins/__init__.py 的 _MODULES 保持一致。
PLUGINS = [
    f"convcore.plugins.{m}"
    for m in (
        "image", "image_extra", "vector", "media",
        "document", "document_pdf", "document_word", "document_build",
        "markup", "markup_read", "markup_write", "office", "office_soffice",
        "data_tree", "data_table", "archive", "font",
    )
]

HEAVY = (
    collect_submodules("PIL")
    + collect_submodules("py7zr")
    + collect_submodules("fontTools")
    + collect_submodules("resvg_py")
    + collect_submodules("rawpy")
    + collect_submodules("rarfile")
    # pyarrow 是动态 import（Parquet 读写），静态分析看不见。
    # 打包环境装了它就该进包；没装时 collect_submodules 返回空，无害。
    + collect_submodules("pyarrow")
)

def strip_bloat(collection):
    """剔除用不到的重型二进制。

    opencv 是 pdf2docx 的硬依赖（版面区域检测），但它捆的视频 IO 后端
    opencv_videoio_ffmpeg*.dll 有 30MB，而 pdf2docx 只做静态图像分析
    （import cv2 as cv，仅用形态学和轮廓那几个函数）。我们的视频转换走
    独立的 ffmpeg 引擎，跟这个 DLL 无关。
    """
    drop = ("opencv_videoio_ffmpeg",)
    return TOC(
        entry for entry in collection if not any(d in entry[0] for d in drop)
    )


a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("convcore/webui/static", "convcore/webui/static"),
    ],
    hiddenimports=[
        *PLUGINS,
        *HEAVY,
        "pillow_heif",
        "psd_tools",
        "pymupdf",
        "pdf2docx",
        "docx",
        "openpyxl",
        "mistune",
        "lxml.etree",
        "yaml",
        "tomli_w",
        "puremagic",
        # tkinter 供 Web UI 的文件对话框子进程用（见 _paths.python_cmd）
        "tkinter",
        "tkinter.filedialog",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 开发期依赖不进包
    excludes=["pytest", "PyInstaller", "setuptools", "pip"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="convcore",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # 桌面应用，不弹黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# 第二个入口：带控制台的 CLI。共享同一份 Analysis，所以不额外占空间——
# COLLECT 把两个 exe 放进同一个目录，依赖只有一份。
# 需要它是因为 console=False 时 sys.stdout 是 None，CLI 的 print 全部失效。
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="convcore-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    exe_cli,
    strip_bloat(a.binaries),
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="convcore",
)
