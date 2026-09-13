/* convcore 桌面端前端。
 *
 * 设计要点：
 * 1. 目标格式**按域分组**，走弹层选择器。全部格式平铺进下拉框没法用。
 * 2. 损失等级在点「开始转换」之前就显示，且是针对每个具体文件算的。
 * 3. 进度由 Python 侧 evaluate_js 推过来（window.onJob），不轮询。
 */

const $ = (id) => document.getElementById(id);
const api = () => window.pywebview.api;

const S = {
  files: [],       // { path, name, size, label, detail, groups, chosen, tierOf, job }
  env: null,
  running: false,
  pickerFor: null, // 正在为哪个文件选目标；'*' 表示批量
};

const TIER_ICON = {
  copy: "⚡", lossless: "✓", lossy: "~", structural: "⚠", inferred: "⚠⚠",
};

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

function size(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i === 0 ? n : n.toFixed(1)} ${u[i]}`;
}

function toast(msg, err = false) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.toggle("err", err);
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3800);
}

/* ── 启动 ── */

async function boot() {
  try {
    S.env = await api().env();
  } catch (e) {
    $("capLine").textContent = "初始化失败";
    toast("初始化失败：" + e, true);
    return;
  }

  const env = S.env;
  $("capLine").textContent = `可读 ${env.readable} · 可写 ${env.writable} 种格式`;

  $("domainChips").innerHTML = env.domains
    .map((d) => `<span class="dChip"><b>${esc(d.label)}</b> ${d.count}</span>`)
    .join("");

  const sel = $("preset");
  sel.innerHTML =
    `<option value="">默认</option>` +
    env.presets.map((p) => `<option value="${p.name}">${esc(p.label)}</option>`).join("");

  if (env.outDir) setOutDir(env.outDir);

  // 提示缺失的必需引擎，但不打扰——用户可能只转图片
  const missing = env.engines.filter((e) => !e.ok && e.tier === "needed");
  if (missing.length) {
    $("btnEngines").textContent = `引擎 ${missing.length}`;
  }
}

function setOutDir(p) {
  $("outPath").textContent = p || "源文件所在目录";
  $("btnOutReset").classList.toggle("hidden", !p);
}

/* ── 添加文件 ── */

async function addPaths(paths) {
  let ok = 0;
  const errs = [];

  for (const p of paths) {
    if (S.files.some((f) => f.path === p)) continue;
    let info;
    try {
      info = await api().inspect(p);
    } catch (e) {
      errs.push(`${p}: ${e}`);
      continue;
    }
    if (info.error) {
      errs.push(`${p.split(/[\\/]/).pop()}：${info.error}`);
      continue;
    }
    if (!info.groups.length) {
      // 门控格式有专门的指引（如 Word 97 缺 LibreOffice）；否则才是真的没目标。
      if (info.gatedHint) {
        errs.push(`${info.name}：${info.gatedHint}`);
      } else {
        errs.push(`${info.name}：没有可用的转换目标`);
      }
      continue;
    }
    info.chosen = info.default;
    info.job = null;
    S.files.push(info);
    ok++;
  }

  render();
  if (errs.length) toast(errs[0] + (errs.length > 1 ? ` 等 ${errs.length} 项` : ""), true);
  else if (ok) toast(`已添加 ${ok} 个文件`);
}

async function pickFiles() {
  try {
    const paths = await api().pick_files();
    if (paths && paths.length) await addPaths(paths);
  } catch (e) {
    toast("打开对话框失败：" + e, true);
  }
}

// 原生拖放的回调，由 Python 侧推过来（带真实路径）
window.onDrop = (paths) => {
  document.querySelectorAll(".hot").forEach((el) => el.classList.remove("hot"));
  if (paths && paths.length) addPaths(paths);
};

/* ── 渲染 ── */

function itemOf(f, mime) {
  for (const g of f.groups) {
    const hit = g.items.find((i) => i.mime === mime);
    if (hit) return hit;
  }
  return null;
}

function render() {
  const has = S.files.length > 0;
  $("empty").classList.toggle("hidden", has);
  $("work").classList.toggle("hidden", !has);
  if (!has) return;

  $("count").textContent = `${S.files.length} 个文件`;
  $("list").innerHTML = S.files.map(rowHtml).join("");

  // 重渲染后重绑事件
  S.files.forEach((f, i) => {
    const t = $(`t${i}`);
    if (t) t.onclick = () => openPicker(i);
    const rm = $(`x${i}`);
    if (rm) rm.onclick = () => { S.files.splice(i, 1); render(); };
    const rv = $(`r${i}`);
    if (rv) rv.onclick = () => api().reveal(f.job.outPath);
    const op = $(`o${i}`);
    if (op) op.onclick = () => api().open_file(f.job.outPath);
    const cx = $(`c${i}`);
    if (cx) cx.onclick = () => api().cancel(f.job.id);
  });

  const pend = S.files.filter((f) => !f.job || f.job.state === "pending").length;
  const btn = $("btnRun");
  btn.disabled = S.running || !pend;
  btn.textContent = S.running ? "转换中…" : "开始转换";

  updateSummary();
}

function updateSummary() {
  const done = S.files.filter((f) => f.job?.state === "done").length;
  const failed = S.files.filter((f) => f.job?.state === "failed").length;
  const lossy = S.files.filter((f) => {
    const it = itemOf(f, f.chosen);
    return it && ["lossy", "structural", "inferred"].includes(it.tier);
  }).length;

  const parts = [];
  if (done) parts.push(`完成 ${done}`);
  if (failed) parts.push(`失败 ${failed}`);
  if (!done && !failed && lossy) parts.push(`${lossy} 项会有信息损失`);
  $("summary").textContent = parts.join(" · ");
}

function rowHtml(f, i) {
  const it = itemOf(f, f.chosen);
  const j = f.job;
  let body = "";

  if (j && (j.state === "running" || j.state === "pending")) {
    const indet = !(j.progress > 0);
    body = `<div class="wide">
      <div class="bar2"><div class="fill${indet ? " indet" : ""}"
        style="${indet ? "" : `width:${(j.progress * 100).toFixed(1)}%`}"></div></div>
      <div class="step">${esc(j.step || "排队中")}</div>
    </div>`;
  } else if (j && j.state === "done") {
    const d = j.srcSize ? ((j.outSize - j.srcSize) / j.srcSize) * 100 : 0;
    const extra = j.extra > 0 ? ` <span class="delta">另 ${j.extra} 个文件</span>` : "";
    body = `<div class="wide">
      <div class="result">
        <span class="ok">✓</span>
        <span>${esc(j.outName)}</span>
        <span class="delta">${size(j.srcSize)} → ${size(j.outSize)}（${d >= 0 ? "+" : ""}${d.toFixed(0)}%）· ${j.elapsed}s</span>
        ${extra}
        <button class="mini" id="o${i}">打开</button>
        <button class="mini" id="r${i}">定位</button>
      </div>
      <div class="pathDesc">${esc(j.pathDesc)}${j.better ? " · 实测优于预估" : ""}</div>
      ${(j.warnings || []).length ? `<div class="warns">${j.warnings.map((w) => `<div class="warn">${esc(w)}</div>`).join("")}</div>` : ""}
    </div>`;
  } else if (j && j.state === "failed") {
    body = `<div class="wide result"><span class="bad">✗ ${esc(j.error || "失败")}</span></div>`;
  } else if (j && j.state === "cancelled") {
    body = `<div class="wide result"><span class="delta">已取消</span></div>`;
  }

  const ctl =
    j && ["running", "pending"].includes(j.state)
      ? `<button class="mini" id="c${i}">取消</button>`
      : j && ["done", "failed", "cancelled"].includes(j.state)
        ? ""
        : `<button class="targetBtn" id="t${i}">
             <span>${it ? esc(it.label) : "选择格式"}</span>
             <span class="caret">▾</span>
           </button>
           ${it ? `<span class="tier ${it.tier}">${TIER_ICON[it.tier]} ${esc(it.tierLabel)}${it.better ? " · 免重编码" : ""}</span>` : ""}
           <button class="mini" id="x${i}">移除</button>`;

  const cls = j?.state === "done" ? " done" : j?.state === "failed" ? " failed" : "";

  return `<div class="row${cls}">
    <div class="fileInfo">
      <div class="fname">${esc(f.name)}</div>
      <div class="fmeta">${esc(f.label)} · ${size(f.size)}${f.detail ? " · " + esc(f.detail) : ""}</div>
    </div>
    <div class="rowCtl">${ctl}</div>
    ${body}
  </div>`;
}

/* ── 目标格式选择器 ── */

function openPicker(index) {
  S.pickerFor = index;
  const f = index === "*" ? S.files[0] : S.files[index];
  $("pickerTitle").textContent = index === "*" ? "统一设置目标格式" : "选择目标格式";
  $("pickerSub").textContent =
    index === "*" ? `应用到 ${S.files.length} 个文件` : f.name;
  $("pickerSearch").value = "";
  renderPicker(f, "");
  $("pickerModal").classList.remove("hidden");
  setTimeout(() => $("pickerSearch").focus(), 30);
}

function renderPicker(f, query) {
  const q = query.trim().toLowerCase();
  const cur = S.pickerFor === "*" ? null : f.chosen;

  const html = f.groups
    .map((g) => {
      const items = g.items.filter(
        (i) =>
          !q ||
          i.label.toLowerCase().includes(q) ||
          i.ext.includes(q) ||
          i.mime.includes(q)
      );
      if (!items.length) return "";
      return `<div class="group">
        <div class="groupName">${esc(g.label)}</div>
        <div class="opts">
          ${items
            .map(
              (i) => `<button class="opt${i.mime === cur ? " on" : ""}"
                data-mime="${esc(i.mime)}"
                title="${esc(i.tierLabel)} · ${esc(i.path)}">
                <span class="dot ${i.tier}"></span>
                <span>${esc(i.label)}</span>
                <span class="ext">${i.ext ? "." + esc(i.ext) : ""}</span>
                <span class="optTier">${TIER_ICON[i.tier]}</span>
              </button>`
            )
            .join("")}
        </div>
      </div>`;
    })
    .join("");

  $("pickerBody").innerHTML = html || `<p class="hint">没有匹配的格式</p>`;

  $("pickerBody").querySelectorAll(".opt").forEach((btn) => {
    btn.onclick = () => {
      const mime = btn.dataset.mime;
      if (S.pickerFor === "*") {
        // 批量：只对支持该目标的文件生效，不支持的保持原选择
        let applied = 0;
        for (const file of S.files) {
          if (itemOf(file, mime)) {
            file.chosen = mime;
            applied++;
          }
        }
        toast(
          applied === S.files.length
            ? `全部设为 ${btn.textContent.trim()}`
            : `${applied}/${S.files.length} 个文件已设置（其余不支持该格式）`
        );
      } else {
        S.files[S.pickerFor].chosen = mime;
      }
      $("pickerModal").classList.add("hidden");
      render();
    };
  });
}

/* ── 转换 ── */

async function run() {
  if (S.running) return;
  const pending = S.files.filter((f) => !f.job || f.job.state === "pending");
  if (!pending.length) return;

  const missing = pending.filter((f) => !f.chosen);
  if (missing.length) {
    toast(`${missing.length} 个文件还没选目标格式`, true);
    return;
  }

  const opts = {};
  const preset = $("preset").value || null;

  let ids;
  try {
    ids = await api().convert(
      pending.map((f) => ({ path: f.path, target: f.chosen })),
      preset,
      opts
    );
  } catch (e) {
    toast("启动失败：" + e, true);
    return;
  }

  pending.forEach((f, i) => {
    f.job = { id: ids[i], state: "pending", progress: 0, step: "排队中" };
  });
  S.running = true;
  render();
}

// Python 侧推来的任务状态
window.onJob = (job) => {
  const f = S.files.find((x) => x.job && x.job.id === job.id);
  if (!f) return;
  f.job = { ...f.job, ...job };

  const active = S.files.some(
    (x) => x.job && ["pending", "running"].includes(x.job.state)
  );
  if (!active && S.running) {
    S.running = false;
    const done = S.files.filter((x) => x.job?.state === "done").length;
    const bad = S.files.filter((x) => x.job?.state === "failed").length;
    toast(bad ? `完成 ${done}，失败 ${bad}` : `${done} 个文件转换完成`, bad > 0);
  }
  render();
};

/* ── 引擎面板 ── */

function renderEngines() {
  const env = S.env;
  const rows = env.engines.map((e) => {
    const cls = e.ok ? "on" : e.tier === "needed" ? "need" : "off";
    const right = e.ok
      ? `<div class="engNote">${esc(e.version || "已就绪")}</div>`
      : `<div class="engNote">装了才能：${esc(e.enables)}${e.size ? ` · ${e.size}` : ""}</div>`;
    const action =
      !e.ok && e.key === "ffmpeg"
        ? `<button class="mini" data-install="${e.key}">安装</button>`
        : !e.ok
          ? `<button class="mini" data-hint="${esc(e.hint)}">怎么装</button>`
          : "";
    return `<div class="engRow">
      <span class="engDot ${cls}"></span>
      <div class="engMain">
        <div class="engName">${esc(e.key)}</div>
        ${right}
      </div>
      ${action}
    </div>`;
  });

  const un = Object.entries(env.unavailable);
  const unHtml = un.length
    ? `<div class="group" style="margin-top:14px">
        <div class="groupName">未启用的插件</div>
        ${un.map(([n, w]) => `<div class="engRow"><span class="engDot off"></span>
          <div class="engMain"><div class="engName">${esc(n)}</div>
          <div class="engNote">${esc(w)}</div></div></div>`).join("")}
      </div>`
    : "";

  $("engineBody").innerHTML =
    `<div class="group"><div class="groupName">外部引擎</div>${rows.join("")}</div>` +
    unHtml +
    `<p class="engNote" style="margin-top:12px">图片、PDF、文档、数据、压缩包、字体都不依赖外部引擎。</p>`;

  $("engineBody").querySelectorAll("[data-install]").forEach((b) => {
    b.onclick = async () => {
      const key = b.dataset.install;
      b.disabled = true;
      b.textContent = "下载中 0%";
      window.onEngineProgress = (k, pct) => {
        if (k === key) b.textContent = `下载中 ${(pct * 100).toFixed(0)}%`;
      };
      try {
        const r = await api().install_engine(key);
        if (r.ok) {
          toast(`${key} ${r.version} 已安装`);
          S.env = await api().env();
          $("capLine").textContent = `可读 ${S.env.readable} · 可写 ${S.env.writable} 种格式`;
          renderEngines();
        } else {
          toast(r.error, true);
          b.disabled = false;
          b.textContent = "重试";
        }
      } catch (e) {
        toast("安装失败：" + e, true);
        b.disabled = false;
        b.textContent = "重试";
      }
    };
  });

  $("engineBody").querySelectorAll("[data-hint]").forEach((b) => {
    b.onclick = () => {
      const hint = b.dataset.hint;
      const url = (hint.match(/https?:\/\/\S+/) || [])[0];
      if (url) api().open_url(url.replace(/[)。，]$/, ""));
      else toast(hint);
    };
  });
}

/* ── 帮助面板：全格式能力矩阵 ── */

let _formatsCache = null;

async function renderHelp() {
  const body = $("helpBody");

  let groups;
  try {
    _formatsCache = _formatsCache || (await api().formats_data());
    groups = _formatsCache;
  } catch (e) {
    body.innerHTML = `<p class="hint">加载失败：${esc(String(e))}</p>`;
    return;
  }

  const total = groups.reduce((n, g) => n + g.count, 0);
  const labels = { raster: "图片", vector: "图片", media: "媒体", document: "文档", markup: "文本", tree: "数据", table: "数据", archive: "压缩包", font: "字体", mesh: "3D" };
  const reachLabel = (k) => labels[k] || k;

  body.innerHTML =
    `<p class="hint" style="margin:2px 0 12px">共 ${total} 种格式。同域内可互转，跨域按下表单向可达。拖入文件后，列表只会显示该文件真正能转的目标。</p>` +
    groups
      .map((g) => {
        const reaches = g.reaches
          .filter((r) => r !== g.key)
          .map(reachLabel)
          .join("、");
        return `<div class="group">
          <div class="groupName">${esc(g.label)} · ${g.count} 种${g.reaches.length > 1 ? ` · 可转去：${reaches ? esc(reaches) : "（同域）"}` : ""}</div>
          <div class="opts">
            ${g.items
              .map(
                (i) =>
                  `<span class="fmtChip${i.readonly ? " ro" : ""}" title="${i.readonly ? "只读（只能转入，不能转出）" : "可读可写"}">` +
                  `<b>${esc(i.label)}</b>` +
                  `<i>${i.ext ? "." + esc(i.ext) : ""}</i>` +
                  `${i.readonly ? `<em>只读</em>` : ""}` +
                  `</span>`
              )
              .join("")}
          </div>
        </div>`;
      })
      .join("") +
    `<div class="group" style="margin-top:16px">
      <div class="groupName">怎么用</div>
      <div class="howTo">
        <p><b>1.</b> 把文件拖进窗口（或点击浏览）。列表会自动识别格式，并给出所有可达目标。</p>
        <p><b>2.</b> 点「选择格式」挑目标。图标是损失等级：<span class="tier copy">⚡ 免重编码</span> <span class="tier lossless">✓ 无损</span> <span class="tier lossy">~ 有损</span> <span class="tier structural">⚠ 丢结构</span> <span class="tier inferred">⚠⚠ 需推断</span>。开始前就知道会丢什么。</p>
        <p><b>3.</b> 需要精细控制时用质量预设（默认 / 小文件 / 高保真…）。命令行用户：<code>convcore-cli convert 文件 -t 目标</code>，<code>formats</code> 看全部格式，<code>targets 文件</code> 看这个文件能转去哪。</p>
      </div>
    </div>`;
}

/* ── 事件绑定 ── */

$("btnPick").onclick = pickFiles;
$("btnPickEmpty").onclick = (e) => { e.stopPropagation(); pickFiles(); };
$("empty").onclick = pickFiles;

$("btnClear").onclick = async () => {
  const active = S.files.filter((f) => f.job && ["pending", "running"].includes(f.job.state));
  if (active.length) await Promise.all(active.map((f) => api().cancel(f.job.id)));
  S.files = [];
  S.running = false;
  render();
};
$("btnRun").onclick = run;
$("btnBulk").onclick = () => { if (S.files.length) openPicker("*"); };

$("btnOutDir").onclick = async () => {
  const p = await api().pick_out_dir();
  if (p) setOutDir(p);
};
$("btnOutReset").onclick = async () => {
  await api().clear_out_dir();
  setOutDir(null);
};
$("btnHelp").onclick = () => {
  renderHelp();
  $("helpModal").classList.remove("hidden");
};
$("helpClose").onclick = () => $("helpModal").classList.add("hidden");

for (const id of ["pickerModal", "engineModal", "helpModal"]) {
  $(id).onclick = (e) => { if (e.target.id === id) $(id).classList.add("hidden"); };
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    $("pickerModal").classList.add("hidden");
    $("engineModal").classList.add("hidden");
    $("helpModal").classList.add("hidden");
  }
});
$("pickerSearch").oninput = (e) => {
  const f = S.pickerFor === "*" ? S.files[0] : S.files[S.pickerFor];
  renderPicker(f, e.target.value);
};

$("btnEngines").onclick = () => {
  renderEngines();
  $("engineModal").classList.remove("hidden");
};
$("engineClose").onclick = () => $("engineModal").classList.add("hidden");

// 拖放视觉反馈。真实路径由 Python 侧的 DOM 事件提供，这里只管高亮。
document.addEventListener("dragover", (e) => {
  e.preventDefault();
  $("empty").classList.add("hot");
});
document.addEventListener("dragleave", (e) => {
  if (!e.relatedTarget) $("empty").classList.remove("hot");
});
document.addEventListener("drop", (e) => {
  e.preventDefault();
  $("empty").classList.remove("hot");
});

window.addEventListener("pywebviewready", boot);
if (window.pywebview?.api) boot();
