# 位图 → 矢量描摹桥（raster → vector）

日期：2026-09-20
状态：已实现（2026-10-08）
范围：跨域桥 + SVG 写出 + 依赖门控。不改 GUI 布局。

## 1. 背景

`everythingcanturn` 用枢纽图做格式互转。`vector.py` 开篇写明：

> 矢量→位图是栅格化（确定性）；位图→矢量是推断。图里只有前者，后者要等专门的描摹插件并标成 `INFERRED`。

`SvgCodec` 目前 `writes = ()`，`BRIDGES` 没有 `raster → vector`，`pipeline._bridge` 也没有对应分支。README 把这条标成推断，图搜索却搜不出来。`KNOWN_ISSUES.md` 把它列为影响最大的功能缺口。

`VectorDoc` 有意保持不透明：只携带 SVG 字节和视口。描摹的产物因此必须是 SVG 文档，写出只是落盘，不必发明第二套矢量对象模型。

## 2. 目标

1. 图标 / Logo / 线稿可以从任意可读位图格式转到 SVG，并出现在 `targets_for` 里。
2. 路径损失为 `INFERRED`。GUI 已有的「需要推断·不可逆」自动生效。
3. 图上出现的目标运行时都能走：缺 `vtracer` 时 PNG→SVG 从列表消失，而不是跑到一半再炸。
4. 照片、渐变、高细节图默认拒绝，提示 `--force`；强制描摹必须留下硬警告。
5. SVG 读写不再绑渲染器。没有 resvg 时，SVG→HTML、SVG→SVG 仍然可用。

## 3. 非目标

- 不暴露描摹旋钮（色数、平滑、层级模式）。v1 只留 `--force`。
- 不写栅格→矢量的自研轮廓算法。
- 不接 Inkscape / potrace / AutoTrace。
- 不做动画 SVG、不做多帧分别描成多文件。
- 不把 JPEG XL、OCR、concat 塞进这次。
- 不为强制描摹加 GUI 复选框（CLI / API 的 `force` 足够；GUI 后续可接同一 option）。

## 4. 已锁定的产品决策

| 项 | 选择 |
|---|---|
| 场景 | 图标 / Logo / 线稿。照片不是一等公民 |
| 引擎 | 可选 Python 包 `vtracer`（Vision Cortex），Windows 有 wheel |
| 非图标 | 启发式 `FAIL`，`--force` 绕过 |
| 架构 | 枢纽桥 `raster ⇒ vector`，不是 DirectConverter |
| 门控粒度 | 按桥的依赖分别门控，不是整颗 `SvgCodec` 上下线 |

## 5. 架构

转换链：

```
PNG --READ--> [raster] --BRIDGE INFERRED--> [vector] --WRITE--> SVG
```

三层职责：

| 层 | 行为 | 依赖 |
|---|---|---|
| `SvgCodec.parse` / `write` | 读写 SVG 字节 | 无 |
| `vector → raster` | 现有 `rasterize()` | 渲染器（resvg_py / resvg / cairosvg） |
| `raster → vector` | 新增 `trace_raster()` | `vtracer` |
| `vector → markup` | 现有嵌入 HTML | 无 |

`SvgCodec.available()` 改为恒真。解析和写出都不需要外部工具；以前绑渲染器，是因为 SVG 当时只能拿去栅格化。

损失落在桥上，不落在写出边上：

- `WRITE vector → image/svg+xml`：`LOSSLESS`（IR 已是 SVG 字节）
- `BRIDGE raster → vector`：`INFERRED`，`time_cost=8.0`，`fidelity=0.9`

路径的 `Path.loss = max(...)` 仍是 `INFERRED`。`fidelity=0.9` 是有意的：让 Logo 的 PNG→HTML 走「描摹再嵌 SVG」，而不是走 `raster→document→markup` 那条抽 PDF 文本的劣路径（保真度惩罚 0.15，且对图标几乎抽不到字）。照片走这条会在执行期被启发式拦住，比产出空 HTML 更诚实；`--force` 可恢复。

不采用 DirectConverter：N 种位图就要 N 条边，也接不上 `raster→vector→markup`。

## 6. 组件

### 6.1 `convcore/graph.py`

- `BRIDGES` 增加一行：
  `("raster", "vector", LossTier.INFERRED, 8.0, 0.9)`
- 注释从「位图→矢量不自动搜出来」改为「自动搜，标 INFERRED，执行期启发式拒绝非图标」。
- 新增 `BRIDGE_GATES: dict[tuple[Domain, Domain], Callable[[], bool]]`：
  - `("vector", "raster")` → `has_svg_renderer()`
  - `("raster", "vector")` → `has_vtracer()`
- 新增 `bridge_enabled(src, dst) -> bool`：无门控则真；有门控则调探测函数。
- `FormatGraph.__init__(registry, *, gate_bridges: bool = True)`。`gate_bridges=False` 时只看域是否活着，供 `tests/test_graph.py` 的假插件图使用，避免单测绑真实依赖。
- `_build`：`a in live and b in live and (not gate_bridges or bridge_enabled(a, b))` 才加边。

`cli.py` 的 `formats` 和 `desktop/app.py` 的域间「可转去」 presently 只检查域存活。改为同时走 `bridge_enabled`，否则帮助面板会宣称 `raster → vector` 在没装 `vtracer` 时也通。

### 6.2 `convcore/plugins/vector.py`

导出探测函数（`graph.py` 懒加载，避免模块顶层循环导入）：

- `has_svg_renderer()`：现有 `_has_resvg_py() or _find_resvg() or _has_cairosvg()`
- `has_vtracer()`：`import vtracer` 成功则为真

`SvgCodec`：

- `writes = caps("image/svg+xml")`（默认 `LOSSLESS`）
- `write(ir, target, options)`：校验 `isinstance(ir, VectorDoc)`，返回 `[Resource(media_type=target, data=ir.data)]`
- `available()` 返回 `(True, "")`

新增 `trace_raster(raster, opts, pol) -> VectorDoc`，由 `pipeline._bridge` 在 `("raster", "vector")` 时调用。

### 6.3 `convcore/pipeline.py`

`_bridge` 增加分支，对称于 `vector → raster`。`ConversionError` 原样抛出（启发式拒绝不要再包一层「插件 内置」）。其它异常仍走现有 `except Exception` 包装。

### 6.4 CLI / doctor / extras

- `convert` 增加 `--force`：写入 `options["force"]=True`。帮助文案：「允许对不像图标的位图强制描摹」。与 `engines --force` 分属不同子命令，不冲突。
- `doctor` 增加「跨域桥」一小节，列出带门控的桥及失败原因。`vtracer` 不是 Codec，不会进入 `registry.unavailable`，必须单独报。
- `pyproject.toml` 的 `vector` extra 与 `all` extra 都加入 `vtracer>=0.6`。

### 6.5 文档

实现完成后同步：

- `README.md`：跨域表补 `raster → vector`，标明 `INFERRED` 与 `vtracer` 依赖
- `CHANGELOG.md` `[Unreleased]`
- `KNOWN_ISSUES.md`：删除「位图 → 矢量桥接未实现」；可选依赖表补 `vtracer`
- `vector.py` / `graph.py` 顶部过时注释

## 7. 数据流（`trace_raster`）

输入：`Raster`。输出：`VectorDoc`。步骤固定，不允许静默跳步。

1. **选帧**
   - ICO / 多尺寸：取 `width * height` 最大的一帧（不是 `frames[0]`，ICO 常把 16px 放在最前）。
   - 动图（`is_animated`）：取首帧，`pol.note(..., Strategy.EMULATE, "描摹只使用第一帧，时间轴不保留")`。
   - 非动画多页（多页 TIFF 等）：只用主帧；`len(frames) > 1` 时记 `EMULATE`，「只描了第一页」。
   - 其它：主帧。

2. **几何**
   - 先走现有 `apply_resize`（尊重 `width` / `height` / `max_edge` / `scale`）。
   - 若最长边仍 `> 2048`：缩到 2048，LANCZOS，并 `pol.note(..., Strategy.APPROXIMATE, "描摹前缩到 2048px 边长")`。
   - 2048 是速度上限，不是质量旋钮。

3. **空图**
   - 宽或高为 0，或 RGBA 下 `max(alpha)==0`：`ConversionError("图像是空的或全透明，无法描摹")`。

4. **启发式**（见第 8 节）
   - 未过且 `not opts.get("force")`：`ConversionError`，文案必须包含「不像可描摹的图标」和 `--force`。
   - `force=True`：继续，并 `pol.note(..., Strategy.APPROXIMATE, "已强制描摹，结果可能是色块海报而非可用矢量")`。

5. **模式选择**
   - 图像二值（按 128 阈值灰度化不丢信息）：`colormode="binary"`。
   - 否则：`colormode="color"`。
   - 实现修正（2026-10-08）：原设计用「唯一色 <= 4」判二值，会误判彩色
     Logo——vtracer 的 binary 模式只认黑前景，红圆这类被阈值吃掉、产出空
     SVG（实测 0.6.12）。改为「阈值化无损」判定后才启用 binary。

6. **调用 vtracer**
   - 主帧以 PNG 写出到 `opts["_tmp"]`（保留 alpha；与 resvg exe 落盘同一约定）。
   - 调用 `vtracer.convert_image_to_svg_py(src, dst, **params)`。
   - 读回 SVG 字节。失败则 `ConversionError`，带上引擎原文。
   - 若产物不含 `<svg`：视为引擎失败。
   - 若产物没有任何轮廓元素（path/rect/circle/ellipse/polygon）：报错，不返回空壳 SVG。

7. **返回** `VectorDoc(data=svg_bytes, source_type=raster.source_type, width=..., height=...)`。

元数据（EXIF / ICC）不写入 SVG。`keep_metadata` 对此桥无意义，不额外告警——SVG 没有对应槽位，与「不透明扩展槽」原则一致：映射不到就丢，且用户选的是推断路径。

## 8. 启发式（可测、有常量名）

在 **128px 最长边** 的 RGB 缩略图上计算（LANCZOS）。透明像素先合成到白底，只用于判定，不改真正描摹的图。

```
unique = 缩略图唯一 RGB 元组数
palette = thumb.quantize(colors=16, method=MEDIANCUT)
mse    = mean((thumb - palette.convert("RGB"))²)   # 对所有像素、三通道
```

常量：

- `TRACE_UNIQUE_PASS = 48`：唯一色少于此值直接通过（抗锯齿 Logo 常见 20–80，取偏严的下限作绿灯）。
- `TRACE_PALETTE_MSE_MAX = 80.0`：16 色重建的均方误差上限。平坦 Logo 通常 < 40；照片通常 > 200。

判定：

```
pass = unique <= TRACE_UNIQUE_PASS or mse <= TRACE_PALETTE_MSE_MAX
```

理由：抗锯齿会把唯一色抬上去，但 16 色量化误差仍然低；照片两个条件都失败。阈值是实现常量，测试用夹具钉死行为，不在 v1 做成选项。

## 9. vtracer 默认参数

面向 Logo，不面向照片：

| 参数 | 值 | 原因 |
|---|---|---|
| `colormode` | `color` 或 `binary` | 见第 7 步 |
| `hierarchical` | `stacked` | 重叠色块，适合 Logo |
| `mode` | `spline` | 贝塞尔，真矢量而不是折线 |
| `filter_speckle` | `4` | 去噪点，不吃细线 |
| `color_precision` | `6` | 够用，避免颜色爆炸 |
| `layer_difference` | `16` | 默认分层 |
| `corner_threshold` | `60` | 默认 |
| `length_threshold` | `4.0` | 默认 |
| `max_iterations` | `10` | 默认 |
| `splice_threshold` | `45` | 默认 |
| `path_precision` | `3` | 文件体积与平滑的折中 |

v1 不把这些映射到 CLI。以后要做质量档，再加 preset，不加十二个旗标。

## 10. 失败与降级

| 情况 | 行为 |
|---|---|
| 未装 `vtracer` | 图上无 `raster→vector`；`targets_for(image/png)` 不含 SVG；`find_path` 抛现有 `NoPathError` |
| 未装渲染器 | 无 `vector→raster`；SVG 读写和 SVG→HTML 仍在 |
| 不像图标 | `ConversionError`，提到 `--force` |
| `--force` 硬描 | 产出 SVG + `APPROXIMATE` 警告 |
| 动图 | 一帧 + `EMULATE` 警告 |
| 空图 / 全透明 | 拒绝，不写空 `<svg>` |
| `vtracer` 抛错或产物非法 | `ConversionError`，附引擎原文 |
| 桥表有边、实现漏了 | 现有「跨域桥接尚未实现」兜底；本次必须把分支补上，测试锁住 |

## 11. 测试

原则：不 mock「图上有边」来假装能转。集成测试 `pytest.importorskip("vtracer")`；图论测试用假插件 + `gate_bridges=False`。

必须覆盖：

1. **图声明**（`test_graph.py`）
   - 改写 `test_cross_domain_needs_declared_bridge`：假 vector codec **带 write** 时，PNG→SVG 可达且 `loss is INFERRED`，边为 `READ, BRIDGE, WRITE`。
   - `gate_bridges=True` 且门控返回假：PNG→SVG 抛 `NoPathError`。
   - `gate_bridges=True` 且门控返回真：可达。
   - 现有 `test_bridge_enables_vector_to_raster` 继续用 `gate_bridges=False`，不绑 resvg。
   - `make_graph` 默认 `gate_bridges=False`。

2. **SvgCodec 不再绑渲染器**
   - monkeypatch 掉三种渲染器**之后**再构造 `Converter`（图在 `__init__` 里建一次）。此时 SVG→HTML 仍通，SVG→PNG 从 `targets_for` 消失。需要 markup 插件。

3. **集成**（`tests/test_raster_to_vector.py`，缺 vtracer 则 skip）
   - 少色 Logo：纯色圆 / 矩形 PNG → SVG，含 `<svg` 以及 `path` 或 `polygon`/`circle`/`rect`。
   - `targets_for("image/png")` 含 `image/svg+xml`；`path` 描述含 `⇒`。
   - 高噪声照片（随机 RGB 256×256）：无 `force` 失败，消息含「不像」；有 `force` 成功且 `warnings` 非空。
   - 透明 PNG（色块 + 透明底）：产物 SVG 不是整页不透明矩形底（断言 viewBox/path 存在，且不全是一张铺满的不透明 rect 作为唯一元素；允许 stacked 色块）。
   - 全透明图：失败。
   - 动图 GIF：成功，警告含「帧」。

4. **门控可见性**
   - monkeypatch `has_vtracer` 为假后，真实图的 `targets_for("image/png")` 不含 SVG。
   - `formats` 的 raster「可转去」在门控关闭时不含 vector。

不测：vtracer 内部曲线是否「好看」；不对比 Inkscape。语义是「能出合法 SVG、图标能出路径、照片默认拒绝」。

## 12. 将改动的文件

| 文件 | 改动 |
|---|---|
| `convcore/graph.py` | 新桥、门控、`bridge_enabled`、`gate_bridges` |
| `convcore/plugins/vector.py` | 探测函数、`write`、`trace_raster`、启发式、`available` |
| `convcore/pipeline.py` | `_bridge` 分支 |
| `convcore/cli.py` | `--force`；`formats` 走门控；`doctor` 桥状态 |
| `convcore/desktop/app.py` | 域可达走 `bridge_enabled` |
| `pyproject.toml` | `vtracer>=0.6` 进 `vector` 与 `all` |
| `tests/test_graph.py` | 假图默认不门控；改反例；补门控测 |
| `tests/test_raster_to_vector.py` | 新建 |
| `tests/test_svg_to_html.py` | 可补「无渲染器仍能嵌 HTML」 |
| `README.md` / `CHANGELOG.md` / `KNOWN_ISSUES.md` | 同步 |

## 13. 风险与交互

- **PNG→HTML 路径切换**：装上 `vtracer` 后，图标 PNG→HTML 会改走描摹。这是期望。照片 PNG→HTML 可能从「抽到空文档」变成启发式失败。接受，并在 `KNOWN_ISSUES` 留一句。
- **`vector` extra 变重**：现在只装 `resvg-py`。加入 `vtracer` 后，只想栅格化的用户会多一个包。可接受：extra 名就是整个矢量能力。不为此再拆 `vector-trace` extra（YAGNI）。
- **ICO 选帧**：若现有 ICO 读取已按尺寸降序，行为不变；测试用多尺寸 ICO 钉「描最大面」。
- **graph 懒导入 vector**：`bridge_enabled` 必须在函数体内 import，避免 `graph` 在插件加载前把 `vector` 拉起来形成注册时序问题。`FormatGraph._build` 发生在 `Converter()`，此时插件已 `load_all`，安全。

## 14. 完成标准

- 装了 `.[vector]` 的环境：`convcore convert icon.png -t svg` 产出可打开的 SVG，退出码 0，损失档 `INFERRED`。
- 未装 `vtracer`：`convcore targets icon.png` 无 SVG；`doctor` 说明缺什么。
- 随机照片无 `--force` 非 0 退出；有 `--force` 为 0 且 stderr/警告可见。
- 现有测试全绿；新测试覆盖第 11 节。
- 不再出现「列表里能选、运行才说尚未实现」。
