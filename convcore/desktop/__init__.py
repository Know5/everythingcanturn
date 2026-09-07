"""桌面客户端外壳。

为什么用 pywebview 而不是 Tauri：这台机器缺 MSVC linker 和 Windows SDK 导入库，
Rust 编译不出来，装 Build Tools 要几个 GB。pywebview 只 0.5MB，复用系统已装的
WebView2 运行时，开的是真正的原生窗口。

同一套 HTML/CSS/JS 将来照样能搬进 Tauri——只是把 js_api 桥换成 Tauri 的
invoke 命令。所以这不是白做的过渡方案。

相对浏览器版的关键改进：
- 原生拖放能拿到**真实文件路径**（浏览器安全模型只给文件名）
- 原生文件对话框，不必起 HTTP 服务做中转
- 无地址栏、无标签页，是个真正的应用窗口
"""

from .app import run

__all__ = ["run"]
