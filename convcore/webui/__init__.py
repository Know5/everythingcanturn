"""本地 Web UI。

为什么先做 Web 而不是等 Tauri：这台机器缺 MSVC linker，Tauri 编译不出来。
但 WebView2 运行时是装了的，所以这套 HTML/CSS/JS 将来直接搬进 Tauri 的 webview
就是成品，不是白做的原型。

安全边界（本地服务同样需要）：
- 只绑 127.0.0.1，绝不 0.0.0.0
- 随机端口 + 随机 token，所有 API 都要校验
- 校验 Origin，防止别的网页通过浏览器打我们的接口
"""

from .server import serve

__all__ = ["serve"]
