/* pywebview 桥：API 调用 + Python→JS 事件的注册与缓冲。
 *
 * Python 侧在工作线程用 evaluate_js 推事件（window.onDrop / onJob / onEngineProgress），
 * 事件可能在 React 挂载前就到达，所以这里先注册全局函数，把事件缓冲到 React 绑定处理函数时再补发。
 *
 * 拖放的关键约束（来自 app.py，实测结论）：必须在 **Python 侧**注册 DOM drop 事件处理器
 * （window.dom.document.events.drop），pywebview 才会往 file 对象注入 pywebviewFullPath。
 * 本文件的 onDrop 只是被 Python 通知后的前端出口，不负责拿路径。
 */

import type { Job, PywebviewApi } from "./types";

declare global {
  interface Window {
    pywebview?: { api: PywebviewApi };
    onDrop?: (paths: string[]) => void;
    onJob?: (job: Job) => void;
    onEngineProgress?: (key: string, pct: number, done: number, total: number) => void;
  }
}

type DropHandler = (paths: string[]) => void;
type JobHandler = (job: Job) => void;
type EngineHandler = (key: string, pct: number, done: number, total: number) => void;

let dropHandler: DropHandler | null = null;
let jobHandler: JobHandler | null = null;
let engineHandler: EngineHandler | null = null;
const pendingDrops: string[][] = [];

window.onDrop = (paths) => {
  if (dropHandler) dropHandler(paths);
  else pendingDrops.push(paths);
};
window.onJob = (job) => jobHandler?.(job);
window.onEngineProgress = (key, pct, done, total) => engineHandler?.(key, pct, done, total);

export function bindDrop(fn: DropHandler): void {
  dropHandler = fn;
  pendingDrops.splice(0).forEach(fn);
}

export function bindJob(fn: JobHandler): void {
  jobHandler = fn;
}

export function bindEngine(fn: EngineHandler): void {
  engineHandler = fn;
}

/** 等 pywebview 注入完成；已注入则立即 resolve。 */
export function ready(): Promise<void> {
  if (window.pywebview?.api) return Promise.resolve();
  return new Promise((resolve) => {
    window.addEventListener("pywebviewready", () => resolve(), { once: true });
  });
}

export function api(): PywebviewApi {
  const bridge = window.pywebview?.api;
  if (!bridge) throw new Error("pywebview 未就绪");
  return bridge;
}

// HTML5 拖放的硬性前提（P0 实测）：页面必须在 dragenter/dragover 上 preventDefault，
// 否则浏览器不会派发 drop 事件，pywebview 的 Python 侧监听再正确也拿不到路径。
// 旧 desktop.js 一直有这两行；spike 第一版漏掉，症状是“拖进去完全没反应”。
window.addEventListener("dragenter", (e) => e.preventDefault());
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => e.preventDefault());
