# 桌面端 GUI 重构：React + Ant Design

日期：2026-10-29
状态：计划中（P0 验证未跑）
范围：只替换桌面端前端与加载方式。转换核心、CLI、浏览器版 webui 的功能不变。

## 1. 背景

桌面端现在是手写的三件套：`desktop.html`（121 行）+ `desktop.js`（578 行）+ `desktop.css`（468 行），
由 `convcore/desktop/app.py` 用 pywebview 承载。功能骨架是齐的（拖放、按域分组的目标选择、
损失档位、逐文件进度、预设、输出目录、引擎安装、格式矩阵），问题在视觉与交互是原生 DOM 手写，
观感简陋、扩展成本高。

主流程只有一个，必须保住的体验是：

> 拖文件进来 → 立刻看到这个文件能转成什么（带损失档位）→ 选中转掉 → 打开产物

## 2. 目标

1. 用 React + TypeScript + Ant Design 重写桌面端前端，观感和交互达到"愿意天天打开"的程度。
2. 第 3 节的 13 个 API 方法、3 个事件的名字与语义**不改变**（后端零改动，除加载方式）。
3. 现有全部行为对等（第 11 节清单），不做功能删减。
4. 纯离线：不引 CDN、不引网络字体、运行时不发任何外部请求。
5. 打包链路保持：`convcore.spec` 的 `datas` 已包含 `convcore/webui/static`，产物放进其子目录
   即自动进包，spec 尽量不改。

## 3. 非目标

- 不重构浏览器版 `convcore/ui`（webui/static 的 index.html + app.js）。桌面端稳定后另立计划；
  将来统一需要一层 `api()` 适配器（pywebview vs HTTP），不在本计划内。
- 不改任何转换逻辑、插件、图搜索。
- 不做 i18n、不做多主题切换（先保持深色）、不做响应式/移动端。
- 不引入路由、Redux、SSR、UI 组件库自研。
- 不新增后端功能（无重试按钮、无历史页、无设置页——保持对等，超出的另开）。

## 4. 技术选型与理由

| 项 | 选择 | 理由 |
|---|---|---|
| 框架 | React 18 + TypeScript | 生态与 AntD 匹配；TS 把 API 契约固化在编译期 |
| 组件库 | Ant Design 5（`@ant-design/icons`） | 用户指定；暗色 algorithm + zh_CN locale 开箱可用 |
| 构建 | Vite 5 | 快、零配置 TS；产物可控（见第 6 节） |
| 状态 | Zustand | 逐文件进度高频更新，selector 订阅避免整树重渲染；比 Redux 轻 |
| 产物位置 | `convcore/webui/static/gui/` | 现有 `convcore.spec` 的 datas 自动覆盖，打包零改动 |
| 产物形态 | IIFE、相对路径（`base: './'`）、文件名带 hash | 见第 6 节：file:// 下 ESM 会被 CORS 拦；hash 防旧缓存 |

## 5. 冻结的接口契约（不得改动）

API（`window.pywebview.api.*`，全部已有）：

| 方法 | 输入 | 返回 | 前端用途 |
|---|---|---|---|
| `env()` | — | readable/writable/plugins/unavailable/domains/engines/presets/outDir | 启动、能力摘要、预设、引擎面板 |
| `formats_data()` | — | 按域分组的格式矩阵 | 帮助面板 |
| `pick_files()` | — | 路径数组 | 浏览按钮 |
| `pick_out_dir()` | — | 路径或 null | 输出目录 |
| `clear_out_dir()` | — | — | 重置输出目录 |
| `inspect(path)` | 路径 | 文件信息 + 分组目标 + 默认目标 + gatedHint | 拖入/选择后解析 |
| `convert(items, preset, opts)` | 数组 | 任务 id 数组 | 开始转换 |
| `cancel(jid)` / `cancel_all()` | 任务 id | bool/数量 | 取消 |
| `reveal(path)` / `open_file(path)` | 产物路径 | bool | 定位/打开产物 |
| `open_url(url)` | https 链接 | bool | 引擎安装指引 |
| `install_engine(key)` | 引擎 key | {ok,...} | 引擎安装 |

事件（Python 侧 `evaluate_js` 推送，运行在工作线程）：

| 全局函数 | 时机 | 处理要点 |
|---|---|---|
| `window.onDrop(paths)` | 原生拖放拿到真实路径 | 可能在 React 挂载前到达 → 桥接层缓冲 |
| `window.onJob(job)` | 任务状态/进度 | 按 `job.id` 匹配，天然幂等 |
| `window.onEngineProgress(key,pct,done,total)` | 引擎下载进度 | 合并到引擎面板状态 |

拖放的关键约束（来自 `app.py` 注释与 P0 实测）：

1. **前端必须在 `dragenter`/`dragover` 上 `preventDefault()`**，否则浏览器不派发 `drop` 事件，
   Python 侧监听再正确也收不到。旧 `desktop.js` 一直有这两行；spike 第一版漏掉，症状就是
   “拖进去完全没反应”。新前端按 `bridge.ts` 模块级监听处理，不依赖 React 挂载。
2. 必须在 **Python 侧**注册 DOM drop 事件处理器（`window.dom.document.events.drop`），
   pywebview 才会往 file 对象里注入 `pywebviewFullPath`（见 `webview/util.py` 的
   `pywebviewEventHandler` 分支）。前端换框架后这套挂法必须原样保留。
3. 这个全局保护是**应用级不变量**：`main.tsx` 必须 `import "./bridge"`，不能依赖
   某个界面组件恰好引用了它（P1 视觉稿第一版就漏了，症状是拖入文件被 WebView2
   直接打开——默认导航到 file://）。

## 6. 加载方案：P0 必须先验，三选一

现状是 `html=_ui_html()`，把 CSS/JS 内联进一个自包含 HTML。这么做是为了绕开 WebView2
的 file:// 缓存（v1.0→v1.1 升级时真实踩到旧 JS 不生效）。但 AntD bundle 内联会顶到两个天花板：

- WebView2 的 `NavigateToString` **有 2MB 上限**（待 spike 用实测确认）；
- React + AntD + icons 压缩后约 1～2MB，单文件内联很容易超。

候选方案（按优先级验）：

| 方案 | 做法 | 优点 | 风险 |
|---|---|---|---|
| **A. file://** | `url=file:///…/gui/index.html`（**不能带查询串**），资源相对路径、IIFE | 改动最小，不需要服务器；P0 实测可行 | file:// 下 ESM 被 CORS 拦（所以必须 IIFE）；.NET Uri 会把 `?` 转义成 `%3F`，不能靠 `?v=` 防缓存 |
| **B. 本地 HTTP** | 给 `create_window` 传本地文件路径，pywebview 自带服务器托管（实测 `http://127.0.0.1:<随机端口>/index.html`） | 无长度/协议限制；HTTP 下可带查询串 | 多一个常驻服务；缓存头由 pywebview 控制 |
| **C. 内联单文件** | 保持 `html=`，用 vite-plugin-singlefile 输出单 HTML | 后端几乎不用动 | 2MB 上限（当前 bundle 700KB，暂时可行但会随功能增长） |

**P0 spike 验收（缺一不可）：**
1. 窗口加载构建产物，样式/组件正常，控制台无 CORS/白屏错误；
2. `api().env()` 有返回（js_api 在任何加载方式下都要可用）；
3. 拖入一个真实文件，界面显示**完整路径**（DOM drop 注入没被破坏）；
4. Python 线程 `evaluate_js` 推的事件能更新 React 界面（反向通道）；
5. DevTools Network 无任何外部请求（纯离线）。

spike 工件：`ui/spike/spike.py`（独立脚本，不碰 `app.py`；P0 验收完成后已删除）。

### P0 实测结论（2026-10-29，pywebview 6.2.1，Windows/WebView2）

| 方案 | 结果 | 证据 |
|---|---|---|
| A. `file://` 纯 URI | ✅ 可用：React 挂载、`window.pywebview.api` 注入、`env()` 往返正常 | selftest `root.children=1 title='convcore'`；三窗口对照脚本 |
| A'. `file://` + `?v=` | ❌ 不可用：.NET `Uri` 把 `?` 转义成 `%3F`，导航到不存在的文件名（`chrome-error://chromewebdata/`） | 同上 |
| B. 本地路径（pywebview 自带服务器） | ✅ 可用：`http://127.0.0.1:<随机端口>/index.html` | 同上 |
| 缓存：升级后 index.html 是否陈旧 | ✅ 不陈旧：两次进程间改写 index.html 的标记脚本，第二次读到的就是新内容（`private_mode=False`） | `/tmp/cache_test.py` 两轮运行 |
| 产物体积 | 701.48 KB JS（gzip 224.05 KB）+ 0.4 KB HTML | `npm run build` 输出 |

**决定：采用方案 A（纯 file:// URI）。** 理由：改动最小、无额外常驻服务、实测四项验收全过。
防缓存策略从"给 URL 加版本"改为"资源内容 hash 文件名"（Vite 默认行为）+ 上述实测证明主文档不陈旧。
方案 B 记为回退选项（若以后发现 file:// 在别的机器/策略下受限）。

## 7. 目录与构建

```
ui/                       # 前端源码（新）
  package.json            # npm ci 用，lockfile 必须提交
  vite.config.ts          # base './'；IIFE；outDir 指到 static/gui
  tsconfig.json
  index.html
  src/
    main.tsx              # ConfigProvider(zhCN + dark) + App
    App.tsx
    bridge.ts             # pywebview api 包装 + onDrop/onJob/onEngineProgress 缓冲
    types.ts              # 与 app.py 返回结构一一对应的 TS 类型
    store.ts              # zustand：files/env/jobs/ui
    components/           # TopBar / DropZone / FileList / FileRow / TargetPicker / RunBar / EnginePanel / HelpPanel
    styles.css
convcore/webui/static/gui/  # 构建产物（gitignore；convcore.gui 启动时存在才用新 UI）
```

构建命令：

```bash
cd ui
npm ci          # 首次/CI
npm run build   # 输出到 ../convcore/webui/static/gui
```

`desktop/app.py` 的改动只允许两处（P2 起）：
1. 启动时优先用 `static/gui/index.html`（加载方式按 P0 结论）；产物缺失时报可操作的错误
   （"先在 ui/ 里 npm run build"）。旧内联界面与 `CONVCORE_LEGACY_UI` 回退开关已在 P5 删除；
2. 新增开发态开关：`convcore gui --ui-dev http://localhost:5173`，让 pywebview 指向 Vite dev server，
   保留 js_api 和拖放注入，UI 开发不用反复 build（PyInstaller 路径不受影响）。

## 8. 前端结构

> 设计基线（P1a）：`ui/src/theme.ts` 定义暗色 token、领域/损失配色；`ui/src/mock/MockApp.tsx`
> 是可交互静态稿。原则：单一强调色 + 中性灰分层、低透明度描边代替重阴影、13px 桌面密度、
> 列表行自绘（避免“AntD 默认后台模板感”）。静态稿确认后再接真实数据（P1b）。

- **桥接层**（`bridge.ts`）：`bindDrop/bindJob/bindEngine` 在 React 挂载前注册全局回调并缓冲
  `onDrop`（挂载前的拖放不能丢），避免 StrictMode 二次 effect 导致重复 boot（模块级 once 标志）。
- **状态**（`store.ts`）：`files[]`（含 `info/chosen/job`）、`env`、`ui`（弹层、选择器目标）。
- **接口类型**（`types.ts`）：直接按 `app.py` 的返回结构写；`inspect` 的 groups/items/tier 是核心。
- **渲染映射**：

| 现有 | 新组件 |
|---|---|
| 顶栏（能力行、域 chips、预设 select、引擎/帮助按钮） | `TopBar`：`Space` + `Tag` + `Select` + `Badge` |
| 空态 + 拖放高亮 `.hot` | `DropZone`：`Empty` + 自绘虚线热区（dragover 高亮） |
| 文件行（名称/元信息/目标按钮/损失 Tag/进度/结果/操作） | `FileRow`：`List.Item` + `Button`/`Tag`/`Progress`/`Popconfirm` |
| 目标选择器（按域分组 + 搜索 + 批量模式） | `TargetPicker`：`Modal` + `Input.Search` + 分组列表 + `Segmented` |
| 底部（汇总/输出目录/开始转换） | `RunBar`：`Typography.Text` + `Button type="primary"` |
| 引擎面板（状态/安装/进度/指引） | `EnginePanel`：`List` + `Progress` + `Button` |
| 帮助面板（格式矩阵 + 使用说明） | `HelpPanel`：`Collapse`/`Tabs` + `Tag`（读/写/只读）+ `Descriptions` |
| toast | `App.useApp().message`（错误用 `error`） |

- 主题：`ConfigProvider` + `zhCN` + `theme.darkAlgorithm`，`colorBgBase: '#0f1115'` 延续现有底色；
  `app.py` 的 `background_color` 同步。
- 损失档位图标沿用现有语义（⚡✓~⚠⚠⚠），用 `Tag` 颜色区分。

## 9. 分阶段计划与验收

| 阶段 | 内容 | 验收 | 估算 |
|---|---|---|---|
| **P0** | spike：三种加载方式实测 + 拖放/事件/对话框/离线 | 第 6 节 5 条全过，结论写回本文件 | 0.5d |
| **P1a** | 视觉稿：设计 token（`theme.ts`）+ 可交互静态界面——先把观感定下来 | 用户看着实体窗口提意见，改到满意 | 0.5d |
| **P1b** | 接线：桥接层、store、真实 inspect 数据接进同一套组件 | 视觉稿与真实数据一致；`npm run build` 零错误 | 0.5–1d |

### 已完成（2026-10-29）

- **P0**：加载方式实测已定（方案 A），拖放/事件/http 全部验证通过
- **P1a**：`theme.ts`（Radix 暗色阶梯）+ 界面骨架完成
- **P1b / P2 / P3**：直接接线真实数据，未单独出静态稿——拖放→列表→目标选择→转换→
  进度/结果/打开定位、批量统一目标、预设、输出目录、引擎面板、格式矩阵、清空确认全部可用
- **P4（部分）**：`ci.yml` 加 Node 构建与 “_ui_source 必须指向构建产物” 的断言；
  `release.yml` 加前端构建与打包产物断言；README/CHANGELOG 已更新
- 端到端验证：脚本驱动真实窗口（`window.onDrop` → 列表行出现 → 点主按钮 → 产物行出现），
  结果为 `row-added: True / clicked: 1 / has-done-line: True`

### 遗留

- **P5（已完成 2026-10-30）**：旧前端与回退开关已删除；`ui/package-lock.json` 随前端源码提交
- 原生标题栏暗色只在 Windows 11 生效（DWM 属性调用返回 0 即认为成功，旧系统会退化）
- 打包体积：新前端约 715 KB JS（gzip 230 KB），进包后总体积变化可忽略

| **P2** | 主流程：addPaths/inspect、目标选择器、convert、onJob 进度、取消、打开/定位、汇总 | 第 11 节清单 1–9 通过 | 1–1.5d |
| **P3** | 附属：预设、批量目标、输出目录、引擎面板（含安装进度）、帮助矩阵、清空确认、拖放高亮、ESC | 清单 10–20 通过 | 0.5–1d |
| **P4** | 打包与 CI：package-lock 提交、ci.yml/release.yml 加 Node 构建步骤、缺失产物报错、README 开发说明 | 干净 clone → npm ci && build → `convcore gui` 可开；tag 流水线产出的 zip 里含 gui 静态资源 | 0.5–1d |
| **P5（已完成）** | 对等验收 + 清理：逐条跑清单、删旧前端与回退开关、CHANGELOG/KNOWN_ISSUES 更新 | 清单全过；`git grep desktop.js` 无残留引用 | 0.5d |

总估 3.5–5.5 个工作日（推断，未含能力验收）。

## 10. 打包与 CI 变更

- `ui/package-lock.json` 提交；`node_modules/`、`convcore/webui/static/gui/` 进 `.gitignore`。
- `ci.yml`：新增 setup-node（Node 22、cache npm、`cache-dependency-path: ui/package-lock.json`）+
  `npm ci && npm run build`，让 TS 错误在 PR 阶段暴露；构建产物不参与 Python 测试。
- `release.yml`：PyInstaller 之前插同样的 Node 构建步骤；打包冒烟里加一条"gui/index.html 存在性"检查。
- `convcore.spec`：**不改**——datas 已有 `("convcore/webui/static", "convcore/webui/static")`。
- README「打包」一节补充：先 `cd ui && npm ci && npm run build`。

## 11. 对等清单（P5 逐条验收，来自 desktop.js）

1. 拖入/浏览添加；重复路径去重；失败项汇总成一条 toast（首条 + 计数）。
2. 无目标/gatedHint（如 Word 97 缺 LibreOffice）给出可执行指引。
3. 行内目标按钮显示当前目标 + 损失 Tag（含 "实测免重编码" better 标记）；可移除行。
4. 目标选择器按域分组、同域优先、域内按保真度排序、搜索过滤。
5. 批量模式：只对支持该目标的文件生效，提示"已设置 N/M 个"。
6. 未选目标时拦截；预设选择；输出目录生效。
7. 进度：pending/running 行内进度条（无进度时不确定态动画）+ 步骤文案。
8. 取消单个任务；清空列表时先取消进行中的任务。
9. 完成行：产物名、大小变化 %、耗时、"另 N 个文件"、warnings、打开、定位。
10. 失败/已取消行的展示。
11. 底部汇总：完成 / 失败 / 会有信息损失。
12. 输出目录选择与重置。
13. 引擎面板：就绪状态、版本、缺什么、安装（带进度）、怎么装（https 链接或提示）。
14. 帮助面板：格式矩阵（读/写/只读 + 域可达）、使用说明；数据缓存一次。
15. 能力摘要行 + 域 chips；缺 needed 引擎时按钮提示数量。
16. 预设列表来自 env。
17. ESC / 点遮罩关闭弹层。
18. 空态 ↔ 工作区切换。
19. 窗口 1040×740、最小 760×560、深色背景。
20. 初始化失败有可见错误提示；`pywebviewready` 与"已就绪"两条启动路径不重复 boot。

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| file:// 下 ESM/CORS（方案 A） | 产物固定 IIFE + inlineDynamicImports；P0 先验 |
| NavigateToString 2MB（方案 C） | 放弃内联，走 A/B；P0 实测记录 |
| 拖放注入被新加载方式破坏 | P0 验收第 3 条；必要时保留一个与 React 解耦的 drop shim |
| 事件在 React 挂载前到达 | bridge 缓冲 + job.id 幂等；StrictMode 下 once 标志 |
| WebView2 旧缓存问题复发 | 资源内容 hash 文件名（Vite 默认）+ 主文档不陈旧已实测；file:// 地址严禁带查询串 |
| 构建链把 Node 变成运行依赖 | 产物静态文件入库到 gitignore 目录；运行时只读文件，不需要 Node |
| 大列表频繁进度更新卡顿 | Zustand selector 按行订阅；需要时用 rc-virtual-list（AntD 自带） |

## 13. 回滚

- 旧前端与 `CONVCORE_LEGACY_UI` 开关已删除；需要回滚时按 git 历史恢复，不再保留双界面代码。
- 新 UI 产物是独立目录，删除 `static/gui/` 即回到“缺少构建产物”的明确报错，不影响 Python 代码。

## 14. P0 结论（2026-10-29）

- [x] 加载方案：**A（纯 file:// URI）**——三选一实测见第 6 节；`?v=` 是坑，原因是 .NET Uri 对 file URI 的转义
- [x] file:// 缓存行为：主文档升级不陈旧（`private_mode=False` 实测）；资源靠内容 hash 名防旧
- [x] IIFE 产物体积：701.48 KB（gzip 224.05 KB），距 2MB 上限有空间，但方案 C 仍不采用
- [x] Python→JS 事件（验收 4）：**已复验通过**——点击后第三张卡片出现绿色 `done` 行（该行就是预期结果，无其他副作用）
- [x] 拖放真实路径（验收 3）：**已复验通过**——第一版失败原因是前端未在 `dragover` 上
      `preventDefault()`（`bridge.ts` 已补）；日志证据 `[drop] files=1 paths=1`（Python 注入成功）
- [x] js_api 注入与 `env()` 往返：selftest 已验证（用 DOM 判定；注意 pywebview 的 `evaluate_js` 不 await Promise，不要用 `.then()` 验证）

## 15. 什么会改变本计划

- P0 若 A、B 都挂，只剩内联且超 2MB → 回到方案 B 的变体（Node 侧静态服务）或评估 Tauri；
  本计划作废重写。
- 若实测发现 AntD 在 WebView2（旧内核）上有渲染/性能问题 → 换组件策略（按需局部使用），
  不换目标。
- 若 P2 发现某行为强依赖 DOM 细节、迁移成本超预期 → 把该项降级为"保留一个非 React 小 shim"，
  而不是硬迁移。

## 16. 2026-10-30 产品化视觉重构

P5 删除旧前端后，实际窗口暴露了一个更基础的问题：界面虽然功能对等，但视觉上仍是
“通用后台 / 毕设”气质——平铺列表、底栏操作、AntD 默认控件、首屏一块 0 统计面板。
这一轮只改桌面端前端，不动 Python 行为：

- 布局：顶栏 + 左侧队列 + 右侧任务面板；空状态占满整行，不显示空统计。
- 队列：文件从分隔线行改成独立卡片（扩展名色块、元信息、目标 pill、档位标记、
  进度条、完成态操作、行尾移除）。
- 任务面板：任务统计、信息损失提示、输出设置、本次输出格式汇总、主操作固定在底部。
- 空状态：居中拖放区 + 支持格式 chips + 主按钮，拖入时全屏覆盖提示。
- 目标选择器：搜索 + 按域分组 + 双列格式行，右侧显示档位与批量覆盖数量。
- 引擎/帮助：统一为同一套 section/row 层级，减少 AntD 默认表格感。
- 窄窗口：920/820px 两档压缩侧栏与卡片间距。

验收：真实 pywebview 窗口截图确认空状态、4 文件队列、目标选择器；DOM 驱动验证
`window.onDrop → 目标选择 WebP → 开始转换 → .file-card.is-done + 产物名`。

### 2026-10-30 第二轮：前沿工具感

- 缩略图：`uiservice.inspect_file()` 为图片和 PDF 生成 160px WebP data URI，
  文件卡片直接显示预览；其他格式保留扩展名色块。
- 拖拽排序：文件卡片可拖动，drop 到目标卡片前插入；内部拖动不触发全局
  “添加文件”遮罩。
- 最近完成：完成的任务写入 `localStorage`（最多 6 条），空状态显示最近记录，
  可打开、定位或重新加入队列。
- 快捷键：`Ctrl+O` 添加文件、`Ctrl+Enter` 开始转换；按钮 tooltip 提示。
- 动效：卡片/空态/侧栏在 `prefers-reduced-motion: no-preference` 下淡入上移。
- 标题栏：改为无边框窗口，原生标题栏移除；应用内顶栏承担品牌、状态与窗口控制。
  加载后用 Win32 补回 `WS_THICKFRAME | WS_MAXIMIZEBOX | WS_MINIMIZEBOX` 并
  `SWP_FRAMECHANGED`，恢复边缘缩放（实测边缘 NCHITTEST 返回 HTLEFT/HTRIGHT/HTBOTTOM）；
  拖动区用 pywebview 的 `.pywebview-drag-region`，不启用整窗 easy_drag。
- 图标：用户提供的 512px 彩色文件转换图标，圆角裁透明后生成多尺寸
  `convcore/assets/convcore.ico`，用于 exe、窗口和 favicon；顶栏品牌使用同一图标。
