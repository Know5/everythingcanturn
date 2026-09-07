/* convcore UI 逻辑。
 *
 * 核心：目标格式下拉框的内容是**向注册表查出来的**，不是硬编码。
 * 这是注册表加图搜索架构的直接回报——后端加一个插件，前端自动多出选项，
 * 前端代码一行都不用改。
 *
 * 损失等级在转换前就显示，且是针对这个具体文件算的（MKV→MP4 可能免重编码，
 * 也可能要重压，取决于内部编码）。现有工具大多转完才让你发现。
 */

const TOKEN = document.body.dataset.token;
const $ = (id) => document.getElementById(id);

const state = {
  files: [],      // { path, name, size, label, detail, targets[], chosen, jobId }
  running: false,
  poll: null,
};

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-Token": TOKEN, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: "响应解析失败" }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.toggle("err", isErr);
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3600);
}

function humanSize(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${i === 0 ? n : n.toFixed(1)} ${u[i]}`;
}

const TIER_ICON = {
  copy: "⚡", lossless: "✓", lossy: "~", structural: "⚠", inferred: "⚠⚠",
};

/* ── 环境 ── */

async function loadEnv() {
  try {
    const env = await api("/api/env");
    state.env = env;
    const doms = Object.entries(env.domains)
      .map(([d, n]) => `${({raster:"图片",media:"音视频",document:"文档",table:"数据",vector:"矢量",archive:"归档"})[d] || d} ${n}`)
      .join(" · ");
    $("envLine").textContent = `可读 ${env.readable} 格式 · 可写 ${env.writable} 格式 — ${doms}`;

    const sel = $("preset");
    for (const p of env.presets) {
      const o = document.createElement("option");
      o.value = p.name;
      o.textContent = `${p.label} — ${p.desc}`;
      sel.appendChild(o);
    }
  } catch (e) {
    $("envLine").textContent = "环境检查失败：" + e.message;
  }
}

function renderEnv() {
  const env = state.env;
  if (!env) return;
  const rows = [];

  rows.push(`<div class="envRow"><span class="envKey">格式</span><span>可读 ${env.readable} · 可写 ${env.writable}</span></div>`);
  rows.push(`<div class="envRow"><span class="envKey">插件</span><span>${env.plugins.join("、")}</span></div>`);

  for (const [name, why] of Object.entries(env.unavailable)) {
    rows.push(`<div class="envRow"><span class="envKey"><span class="dot off"></span> ${name}</span><span class="envNote">${escapeHtml(why)}</span></div>`);
  }

  rows.push(`<div class="envRow" style="border-bottom:0;padding-top:14px"><span class="envKey"><b>外部引擎</b></span><span class="envNote">不装只是少对应领域的能力</span></div>`);
  for (const e of env.engines) {
    const cls = e.available ? "on" : (e.tier === "needed" ? "need" : "off");
    const right = e.available
      ? "已就绪"
      : `未装${e.size ? `（${e.size}）` : ""} — 装了才能：${escapeHtml(e.enables)}`;
    rows.push(`<div class="envRow"><span class="envKey"><span class="dot ${cls}"></span> ${e.key}</span><span class="envNote">${right}</span></div>`);
  }

  $("envBody").innerHTML = rows.join("");
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

/* ── 添加文件 ── */

async function addPaths(paths) {
  let added = 0, failed = 0;
  for (const p of paths) {
    if (state.files.some((f) => f.path === p)) continue;
    try {
      const info = await api("/api/inspect", { path: p });
      if (!info.targets.length) { failed++; continue; }
      // 默认选第一个（最保真的），用户可改
      info.chosen = info.targets[0].mime;
      info.jobId = null;
      state.files.push(info);
      added++;
    } catch (e) {
      failed++;
      toast(`${p.split(/[\\/]/).pop()}：${e.message}`, true);
    }
  }
  if (added) render();
  if (failed && !added) toast(`${failed} 个文件无法识别`, true);
}

async function pickFiles() {
  try {
    const { paths } = await api("/api/pick");
    if (paths.length) await addPaths(paths);
  } catch (e) {
    toast("打开选择框失败：" + e.message, true);
  }
}

/* ── 渲染 ── */

function render() {
  const wrap = $("listWrap");
  const drop = $("drop");

  if (!state.files.length) {
    wrap.classList.add("hidden");
    drop.classList.remove("compact");
    return;
  }
  wrap.classList.remove("hidden");
  drop.classList.add("compact");
  $("countLine").textContent = `${state.files.length} 个文件`;

  $("list").innerHTML = state.files.map(itemHtml).join("");

  // 绑事件（重渲染后要重绑）
  for (const [i, f] of state.files.entries()) {
    const sel = $(`t${i}`);
    if (sel) sel.onchange = (e) => { f.chosen = e.target.value; render(); };
    const rm = $(`rm${i}`);
    if (rm) rm.onclick = () => { state.files.splice(i, 1); render(); };
    const rv = $(`rv${i}`);
    if (rv) rv.onclick = () => api("/api/reveal", { path: f.result.outPath });
  }
  $("btnRun").disabled = state.running;
  $("btnRun").textContent = state.running ? "转换中…" : "开始转换";
}

function itemHtml(f, i) {
  const t = f.targets.find((x) => x.mime === f.chosen) || f.targets[0];
  const r = f.result;

  // 目标下拉框：内容来自后端注册表查询，不是硬编码
  const opts = f.targets
    .map((x) => `<option value="${x.mime}"${x.mime === f.chosen ? " selected" : ""}>${x.label}${x.ext ? ` (.${x.ext})` : ""}</option>`)
    .join("");

  let body = "";
  if (r && (r.state === "running" || r.state === "pending")) {
    const indet = r.progress <= 0;
    body = `
      <div class="bar"><div class="barFill${indet ? " indet" : ""}" style="width:${indet ? "" : (r.progress * 100).toFixed(1) + "%"}"></div></div>
      <div class="stepLine">${escapeHtml(r.step || "排队中")}</div>`;
  } else if (r && r.state === "done") {
    const delta = f.size ? ((r.outSize - f.size) / f.size * 100) : 0;
    body = `
      <div class="result">
        <span class="ok">✓</span> ${escapeHtml(r.outPath.split(/[\\/]/).pop())}
        <span class="meta">${humanSize(f.size)} → ${humanSize(r.outSize)}（${delta >= 0 ? "+" : ""}${delta.toFixed(0)}%）· ${r.elapsed}s</span>
        <button class="mini" id="rv${i}">定位</button>
      </div>
      <div class="pathDesc">${escapeHtml(r.pathDesc)}</div>
      ${r.warnings.map((w) => `<div class="warn">${escapeHtml(w)}</div>`).join("")}`;
  } else if (r && r.state === "failed") {
    body = `<div class="result"><span class="bad">✗ ${escapeHtml(r.error || "失败")}</span></div>`;
  } else if (r && r.state === "cancelled") {
    body = `<div class="result"><span class="meta">已取消</span></div>`;
  } else {
    // 未开始：显示转换前的损失预告，这是最关键的设计点
    body = `
      <div class="itemCtl">
        <span>转换为</span>
        <select id="t${i}">${opts}</select>
        <span class="tier ${t.tier}">${TIER_ICON[t.tier]} ${escapeHtml(t.tierLabel)}${t.better ? " · 免重编码" : ""}</span>
        ${!t.reversible ? `<span class="pathDesc">不可逆</span>` : ""}
        <button class="mini" id="rm${i}">移除</button>
      </div>
      <div class="pathDesc">${escapeHtml(t.pathDesc)}</div>`;
  }

  return `
    <div class="item">
      <div class="itemTop">
        <span class="fname">${escapeHtml(f.name)}</span>
        <span class="meta">${escapeHtml(f.label)} · ${humanSize(f.size)}</span>
      </div>
      ${f.detail ? `<div class="detail">${escapeHtml(f.detail)}</div>` : ""}
      ${body}
    </div>`;
}

/* ── 转换 ── */

async function run() {
  if (state.running || !state.files.length) return;
  const pending = state.files.filter((f) => !f.jobId || ["failed", "cancelled"].includes(f.result?.state));
  if (!pending.length) return;
  const items = pending.map((f) => ({ path: f.path, target: f.chosen }));

  try {
    const { ids } = await api("/api/convert", {
      items,
      preset: $("preset").value || null,
    });
    ids.forEach((id, i) => {
      pending[i].jobId = id;
      pending[i].result = { state: "pending", progress: 0, step: "", warnings: [] };
    });
    state.running = true;
    render();
    startPolling(ids);
  } catch (e) {
    toast(e.message, true);
  }
}

function startPolling(ids) {
  clearTimeout(state.poll);
  let active = true;
  const poll = async () => {
    if (!active) return;
    try {
      const { jobs } = await api("/api/progress", { ids });
      const byId = Object.fromEntries(jobs.map((j) => [j.id, j]));
      for (const f of state.files) {
        if (f.jobId && byId[f.jobId]) f.result = byId[f.jobId];
      }
      const done = jobs.every((j) => ["done", "failed", "cancelled"].includes(j.state));
      if (done) {
        active = false;
        clearTimeout(state.poll);
        state.running = false;
        const ok = jobs.filter((j) => j.state === "done").length;
        const bad = jobs.filter((j) => j.state === "failed").length;
        toast(bad ? `完成 ${ok} 个，失败 ${bad} 个` : `${ok} 个文件转换完成`, bad > 0);
      }
      render();
      if (active) state.poll = setTimeout(poll, 500);
    } catch (e) {
      active = false;
      clearTimeout(state.poll);
      state.running = false;
      toast("进度查询失败：" + e.message, true);
      render();
    }
  };
  poll();
}

/* ── 事件绑定 ── */

const drop = $("drop");

["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    drop.classList.add("hot");
  })
);
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => {
    e.preventDefault();
    if (ev === "dragleave" && drop.contains(e.relatedTarget)) return;
    drop.classList.remove("hot");
  })
);

drop.addEventListener("drop", async (e) => {
  // 浏览器安全模型不给真实路径，只给文件名。所以拖放只能提示用选择框——
  // 这是 Web 版的固有限制，将来 Tauri 外壳里拖放能直接拿到路径。
  const names = [...(e.dataTransfer.files || [])].map((f) => f.name);
  if (names.length) {
    toast(`浏览器不提供文件真实路径，请用「选择文件」（Tauri 版本无此限制）`, true);
  }
});

drop.addEventListener("click", (e) => {
  if (e.target.id !== "btnPick") pickFiles();
});
$("btnPick").onclick = (e) => { e.stopPropagation(); pickFiles(); };

$("btnRun").onclick = run;

$("btnClear").onclick = () => {
  const active = state.files.filter((f) => f.jobId && ["pending", "running"].includes(f.result?.state));
  Promise.all(active.map((f) => api("/api/cancel", { id: f.jobId }).catch(() => null)));
  clearTimeout(state.poll);
  state.files = [];
  state.running = false;
  render();
};

$("btnOutDir").onclick = async () => {
  try {
    const { path } = await api("/api/pick-dir");
    if (path) {
      await api("/api/out-dir", { path });
      $("outDirLine").textContent = "输出到 " + path;
    }
  } catch (e) {
    toast(e.message, true);
  }
};

$("btnEnv").onclick = () => { renderEnv(); $("envSheet").classList.remove("hidden"); };
$("btnEnvClose").onclick = () => $("envSheet").classList.add("hidden");
$("envSheet").onclick = (e) => {
  if (e.target.id === "envSheet") $("envSheet").classList.add("hidden");
};

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("envSheet").classList.add("hidden");
});

loadEnv();
