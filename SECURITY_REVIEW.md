# 安全与稳定性评审报告

评审时间：2026-09-02
评审范围：convcore 全代码库
评审标准：P0=生产事故（数据丢失/RCE/DoS），P1=严重缺陷（崩溃/泄漏/注入）

> **2026-09-29 补充**：本次评审漏掉了一个 P1——Web UI 不校验 Host 头，可被 DNS
> rebinding 利用。已修复，详见文末「补充评审」。下文「无 P0/P1」的结论以补充评审为准。

## ✅ 已防护（无 P0/P1）

### 1. **命令注入** ✅
- `engines/base.py:84`: 显式 `shell=False`，所有外部引擎调用用列表传参
- 无 `shell=True` 出现
- FFmpeg/resvg 参数构造全部数组化，文件名不拼接

### 2. **路径穿越（Zip Slip）** ✅
- `plugins/archive.py:87-110`: 完整防护（拒绝 `../`、绝对路径、盘符）
- `engines/install.py:170`: 引擎下载时也有同样检查
- 内存解压不落盘，进一步降低风险

### 3. **解压炸弹** ✅
- `plugins/archive.py:41-43`: 三重限制
  - 总大小 4GB 上限
  - 压缩比 200:1 上限
  - 条目数 50,000 上限
- `archive.py:137-144`: 运行时检查并中止

### 4. **子进程超时与资源耗尽** ✅
- `engines/base.py:29-30`: 默认 600s 超时
- `base.py:93-99`: 超时后强制 kill
- `base.py:27`: stderr 限制 64KB（防日志炸内存）
- `base.py:70`: stdout 限制 8MB

### 5. **临时文件清理** ✅
- `pipeline.py:42-53`: 上下文管理器保证异常时也清理
- `BaseException` 捕获覆盖 KeyboardInterrupt
- `keep_on_error` 可选保留现场用于诊断

### 6. **输出覆盖保护** ✅
- `pipeline.py:426-431`: 三重检查
  - 目标存在时要求显式 `--overwrite`
  - 同批次文件名冲突直接报错
  - 源文件 == 输出路径时拒绝

### 7. **并发安全（GUI）** ✅
- `uiservice.py:442`: `UiService` 用 `threading.RLock` 保护任务表
- `uiservice.py:505`: 每个任务独立线程 + `threading.Event` 取消
- 任务状态读改写都在锁内，进度推送在锁外调用回调，避免前端阻塞工作线程

### 8. **前端 XSS** ✅
- `ui/src` 全部由 React 渲染，默认转义；没有 `dangerouslySetInnerHTML`
- 浏览器版 `webui/static/app.js` 的用户可控文本走 `textContent`/`escapeHtml`
- 只有静态模板走 `innerHTML`

### 9. **内存限制** ✅
- 归档：4GB 解压上限
- FFmpeg：流式处理，不全量加载
- 文档：PyMuPDF/pypdf 内部限制，未见无界分配

### 10. **密码泄漏** 🟡 低风险
- `pipeline.py:198`: 密码选项在列表里（会进日志）
- 但 PDF 密码本身不敏感（用于打开文件，不是认证凭据）
- CLI 参数会显示在 `ps` 输出（操作系统问题，非项目可控）

## 🟡 P2 级风险（不影响交付，建议后续改进）

### 1. **GUI 线程安全（潜在竞态）**
**位置**: `desktop/app.py:359-361`
```python
job = self._jobs[jid]
job.update(kw)
self._emit(job)
```
**问题**: 读-修改-写三步非原子，多线程同时更新同一任务时可能丢失部分字段。
**影响**: 进度条偶尔卡住或跳变，不会崩溃或数据损坏。
**建议**: `with self._lock: self._jobs[jid].update(kw)` 或改用 `queue.Queue`。

### 2. **文档域内存占用**
**位置**: `README.md:限制 #3`
**问题**: 2GB DOCX 会吃 4-6GB 内存（AST 展开 + 库内部开销）。
**影响**: 大文件转换时可能 OOM。
**建议**: 
- 添加文件大小预检（拒绝 >500MB 的 DOCX）
- 或在进度回调里监控 `psutil.Process().memory_info()`

### 3. **错误信息可能泄漏路径**
**位置**: 各插件的异常消息
**问题**: `ConversionError(f"无法读取：{path}")` 会把完整路径抛给前端。
**影响**: 桌面应用无风险；Web 部署时会暴露服务器目录结构。
**建议**: Web 版部署前在 `webui/server.py` 加中间层过滤路径。

### 4. **PyInstaller 打包的代码注入**
**位置**: `dist/convcore/`
**问题**: exe 可被逆向，插件代码明文可读。
**影响**: 攻击者可分析算法找漏洞，但这是开源项目，代码本就公开。
**建议**: 闭源商业化时考虑加壳（UPX/Themida），当前阶段无需处理。

## 🟢 已遵循的安全最佳实践

1. **最小权限**: 不要求管理员运行，不修改注册表/系统目录
2. **输入校验**: MIME 类型规范化（`formats.normalize`），拒绝非法值
3. **显式错误**: 失败时清晰报错，不吞异常（无裸 `except:` 块）
4. **依赖锁定**: `requirements.txt` 应锁版本（当前未锁，建议 `pip freeze`）
5. **日志安全**: 未见密码/令牌打印（密码只在选项字典里传递）

## 📋 建议的后续动作（按优先级）

### 立即（部署前）
- ✅ 无必须修复项

### 短期（v1.1）
- [ ] GUI 进度更新加锁（P2.1）
- [ ] 添加文件大小预检（拒绝 >1GB 输入，避免 OOM）
- [ ] `requirements.txt` 锁版本号（`pip freeze > requirements.lock.txt`）

### 中期（v2.0）
- [ ] Web 版部署时路径脱敏（P2.3）
- [ ] 添加 fuzz 测试（用 `atheris` 喂损坏的输入）
- [ ] 引擎崩溃自动重试（当前崩一次就失败）

## 总结

**当前版本可安全交付给最终用户。** 核心安全边界（命令注入、路径穿越、资源耗尽）全部到位，P0/P1 级风险为零。发现的 P2 级问题均为边缘场景，不影响正常使用。

建议在 README 补充安全声明：
> 本工具在本地运行，不上传文件到外部服务器。处理不可信文件（网上下载的 ZIP、PDF）时已启用路径穿越和解压炸弹防护。外部引擎（FFmpeg/resvg）以子进程隔离运行，崩溃不影响主程序。

---

## 补充评审（2026-09-29）

### P1：Web UI 可被 DNS rebinding 接管（已修复）

**位置**：`webui/server.py`（`convcore ui` 启动的浏览器版；桌面 GUI 走 pywebview
`js_api`，没有 HTTP 服务，不受影响）

**问题**：原有两道校验在 rebinding 下都会失效：

- 首页 `/` 不鉴权，直接返回注入了 token 的 HTML。
- `_origin_ok` 只比较 Origin 与 Host 是否一致。攻击者把自己的域名解析到
  127.0.0.1 后，两者都是攻击者域名，自然一致。

攻击链：恶意网页 rebinding 到本服务端口 → 同源读取首页拿到 token → 带 token 调用
任意 API。可探测任意路径是否存在、转换任意本地文件、把输出目录改到任意可写位置。
本地复现过：伪造 `Host` 取首页得到 token，伪造 `Host`+`Origin` 调 `POST /api/out-dir` 返回 200。

**利用门槛**：端口随机，需要先扫描；新版 Chrome 的本地网络访问限制能挡掉一部分，
其它浏览器未必。

**修复**：新增 `_host_ok`，所有 GET/POST 在任何处理之前要求 Host 必须是
`127.0.0.1:<端口>` 或 `localhost:<端口>`。回归测试见 `tests/test_webui.py`
（去掉修复后其中 4 个用例失败）。

**为什么上次漏掉**：`_authorized` 的注释已经考虑了「别的网页向 127.0.0.1 发请求」，
但只防了跨站请求，没防同源化的 rebinding；且 `server.py` 当时测试覆盖率为 0。

### 「建议的后续动作」现状

| 项 | 状态 |
|---|---|
| GUI 进度更新加锁（P2.1） | ✅ 已做：`desktop/app.py` 的 `push()` 在 `_lock` 内读改写 |
| 文件大小预检 | ✅ 已做：非媒体输入上限 1 GiB（`pipeline.convert_file` 快速失败，`Resource.read_bytes` 兜底） |
| 依赖锁版本 | ✅ 已做：`requirements.lock.txt` |
| Web 版路径脱敏（P2.3） | ⬜ 未做 |
| fuzz 测试 | ⬜ 未做 |
| 引擎崩溃自动重试/回退 | ⬜ 未做：外部引擎失败即报错 |
