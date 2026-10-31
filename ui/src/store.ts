/* 应用状态与动作。所有后端交互集中在这里，组件只读状态、调动作。
 *
 * 行为对齐旧桌面端（spec 第 11 节对等清单），差异只有一处：旧版把"逐个 inspect"
 * 串行 await，这里改成并发（Promise.all），文件多时添加更快；失败仍逐条汇总。
 */

import { create } from "zustand";
import { api, ready } from "./bridge";
import { isLossy } from "./format";
import type { Env, FileInfo, Job, TargetItem } from "./types";

export type FileRow = FileInfo & { chosen: string | null; job: Job | null };

export interface RecentEntry {
  id: string;
  name: string;
  outName: string;
  outPath: string;
  time: number;
}

const RECENT_KEY = "convcore.recent";

function loadRecent(): RecentEntry[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.slice(0, 6) : [];
  } catch {
    return [];
  }
}

function saveRecent(list: RecentEntry[]): void {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 6)));
  } catch {
    // localStorage 在 file:// 或隐私模式下可能不可用，最近记录不是关键路径
  }
}

export interface FormatsGroup {
  key: string;
  label: string;
  count: number;
  items: {
    mime: string;
    label: string;
    ext: string;
    read: boolean;
    write: boolean;
    readonly: boolean;
  }[];
  reaches: string[];
}

export interface ConcatState {
  enabled: boolean;
  target: string | null;
  job: Job | null;
  count: number;
}

export interface ImageMergeState {
  enabled: boolean;
  target: string | null;
  job: Job | null;
  count: number;
}

interface PickerState {
  /** null=关闭；'*'=批量；其余为文件 path */
  target: string | null;
}

interface State {
  ready: boolean;
  bootError: string | null;
  env: Env | null;
  files: FileRow[];
  preset: string;
  running: boolean;
  dragOver: boolean;
  adding: boolean;
  picker: PickerState;
  concat: ConcatState;
  imageMerge: ImageMergeState;
  cueJob: Job | null;
  enginesOpen: boolean;
  helpOpen: boolean;
  formats: FormatsGroup[] | null;
  engineProgress: Record<string, number>;
  recent: RecentEntry[];

  boot: () => Promise<void>;
  addPaths: (paths: string[]) => Promise<string[]>;
  removeFile: (path: string) => void;
  moveFile: (from: string, to: string) => void;
  clearAll: () => Promise<void>;
  setDragOver: (v: boolean) => void;
  setPreset: (v: string) => void;
  openPicker: (target: string | null) => void;
  chooseTarget: (mime: string) => void;
  run: () => Promise<void>;
  runConcat: () => Promise<void>;
  toggleConcat: (enabled: boolean) => void;
  setConcatTarget: (mime: string) => void;
  runImageMerge: () => Promise<void>;
  toggleImageMerge: (enabled: boolean) => void;
  setImageMergeTarget: (mime: string) => void;
  runCue: (cue: string, target: string) => Promise<void>;
  cancelJob: (id: string) => Promise<void>;
  applyJob: (job: Job) => void;
  setOutDir: (dir: string | null) => void;
  pickOutDir: () => Promise<void>;
  resetOutDir: () => Promise<void>;
  openEngines: (v: boolean) => void;
  openHelp: (v: boolean) => void;
  clearRecent: () => void;
  loadFormats: () => Promise<void>;
  installEngine: (key: string) => Promise<void>;
  setEngineProgress: (key: string, pct: number) => void;
}

/** 在某个文件的可达目标里找一个 item */
export function targetOf(file: FileRow, mime: string | null): TargetItem | null {
  if (!mime) return null;
  for (const g of file.groups) {
    const hit = g.items.find((i) => i.mime === mime);
    if (hit) return hit;
  }
  return null;
}

function concatTarget(files: FileRow[]): string | null {
  const first = files.find((f) => f.domain === "media");
  if (!first) return null;
  const media = first.groups.find((g) => g.key === "media");
  if (!media?.items.length) return null;
  const pref = first.mime.startsWith("video/") ? "video/mp4" : "audio/mpeg";
  return media.items.find((i) => i.mime === pref)?.mime ?? media.items[0].mime;
}

function imageMergeTarget(files: FileRow[]): string | null {
  const first = files.find((f) => f.domain === "raster");
  if (!first) return null;
  const raster = first.groups.find((g) => g.key === "raster");
  if (!raster?.items.length) return null;
  for (const mime of ["image/gif", "image/webp", "image/tiff"]) {
    const hit = raster.items.find((i) => i.mime === mime);
    if (hit) return hit.mime;
  }
  return null;
}

export const useStore = create<State>((set, get) => ({
  ready: false,
  bootError: null,
  env: null,
  files: [],
  preset: "",
  running: false,
  dragOver: false,
  adding: false,
  picker: { target: null },
  concat: { enabled: false, target: null, job: null, count: 0 },
  imageMerge: { enabled: false, target: null, job: null, count: 0 },
  cueJob: null,
  enginesOpen: false,
  helpOpen: false,
  formats: null,
  engineProgress: {},
  recent: loadRecent(),

  boot: async () => {
    try {
      await ready();
      const env = await api().env();
      set({ env, ready: true });
    } catch (e) {
      set({ bootError: String(e) });
    }
  },

  addPaths: async (paths) => {
    const known = new Set(get().files.map((f) => f.path));
    const fresh = paths.filter((p) => !known.has(p));
    if (!fresh.length) return [];

    set({ adding: true });
    const results = await Promise.all(
      fresh.map(async (p) => {
        try {
          return await api().inspect(p);
        } catch (e) {
          return { path: p, error: String(e) } as FileInfo & { error: string };
        }
      }),
    );
    set({ adding: false });

    const ok: FileRow[] = [];
    const problems: string[] = [];
    for (const info of results) {
      const name = info.path ? info.path.split(/[\\/]/).pop() : info.path;
      if (info.error) {
        problems.push(`${name}：${info.error}`);
        continue;
      }
      if (!info.groups?.length) {
        // 门控格式给可执行指引（如 Word 97 缺 LibreOffice），比"没有可用目标"有用
        problems.push(`${info.name}：${info.gatedHint ?? "没有可用的转换目标"}`);
        continue;
      }
      ok.push({ ...info, chosen: info.default, job: null });
    }

    if (ok.length) set((s) => ({ files: [...s.files, ...ok] }));
    return problems;
  },

  removeFile: (path) =>
    set((s) => {
      const files = s.files.filter((f) => f.path !== path);
      const media = files.filter((f) => f.domain === "media");
      const images = files.filter((f) => f.domain === "raster");
      return {
        files,
        concat:
          media.length < 2
            ? { ...s.concat, enabled: false, target: null, count: 0 }
            : s.concat,
        imageMerge:
          images.length < 2
            ? { ...s.imageMerge, enabled: false, target: null, count: 0 }
            : s.imageMerge,
      };
    }),

  moveFile: (from, to) =>
    set((s) => {
      if (from === to) return {};
      const fromIdx = s.files.findIndex((f) => f.path === from);
      const toIdx = s.files.findIndex((f) => f.path === to);
      if (fromIdx < 0 || toIdx < 0) return {};
      const files = [...s.files];
      const [moved] = files.splice(fromIdx, 1);
      files.splice(fromIdx < toIdx ? toIdx - 1 : toIdx, 0, moved);
      return { files };
    }),

  clearAll: async () => {
    const active = get().files.filter(
      (f) => f.job && (f.job.state === "running" || f.job.state === "pending"),
    );
    await Promise.all(
      active.map((f) => api().cancel(f.job!.id).catch(() => false)),
    );
    const concatJob = get().concat.job;
    if (
      concatJob &&
      (concatJob.state === "running" || concatJob.state === "pending")
    ) {
      await api().cancel(concatJob.id).catch(() => false);
    }
    const imageJob = get().imageMerge.job;
    if (
      imageJob &&
      (imageJob.state === "running" || imageJob.state === "pending")
    ) {
      await api().cancel(imageJob.id).catch(() => false);
    }
    const cue = get().cueJob;
    if (cue && (cue.state === "running" || cue.state === "pending")) {
      await api().cancel(cue.id).catch(() => false);
    }
    set({
      files: [],
      running: false,
      concat: { enabled: false, target: null, job: null, count: 0 },
      imageMerge: { enabled: false, target: null, job: null, count: 0 },
      cueJob: null,
    });
  },

  setDragOver: (v) => set({ dragOver: v }),
  setPreset: (v) => set({ preset: v }),
  openPicker: (target) => set({ picker: { target } }),

  chooseTarget: (mime) => {
    const { picker, files } = get();
    if (picker.target === "*") {
      // 批量：只对支持该目标的文件生效，不支持的保持原选择
      set({
        files: files.map((f) =>
          targetOf(f, mime) ? { ...f, chosen: mime } : f,
        ),
        picker: { target: null },
      });
    } else {
      set({
        files: files.map((f) =>
          f.path === picker.target ? { ...f, chosen: mime } : f,
        ),
        picker: { target: null },
      });
    }
  },

  run: async () => {
    const { files, preset, running, ready: isReady } = get();
    if (running || !isReady) return;
    const pending = files.filter((f) => !f.job || f.job.state === "pending");
    if (!pending.length) return;

    const items = pending
      .filter((f) => f.chosen)
      .map((f) => ({ path: f.path, target: f.chosen as string }));
    if (!items.length) return;

    let ids: string[];
    try {
      ids = await api().convert(items, preset || null, {});
    } catch (e) {
      set({ bootError: `启动失败：${e}` });
      return;
    }

    const idByPath = new Map<string, string>();
    items.forEach((it, i) => idByPath.set(it.path, ids[i]));

    set({
      running: true,
      files: files.map((f) => {
        const id = idByPath.get(f.path);
        return id
          ? {
              ...f,
              job: { id, state: "pending", progress: 0, step: "排队中" } as Job,
            }
          : f;
      }),
    });
  },

  runConcat: async () => {
    const { files, preset, running, ready: isReady, concat } = get();
    if (running || !isReady || !concat.enabled || !concat.target) return;
    const media = files.filter((f) => f.domain === "media");
    if (media.length < 2) return;
    let ids: string[];
    try {
      ids = await api().concat(
        media.map((f) => f.path),
        concat.target,
        preset || null,
        {},
      );
    } catch (e) {
      set({ bootError: `启动合并失败：${e}` });
      return;
    }
    set({
      running: true,
      concat: {
        ...concat,
        job: {
          id: ids[0],
          name: "音视频合并",
          count: media.length,
          state: "pending",
          progress: 0,
          step: "排队中",
        } as Job,
      },
    });
  },

  toggleConcat: (enabled) => {
    const files = get().files;
    const media = files.filter((f) => f.domain === "media");
    if (enabled && media.length < 2) return;
    set((s) => ({
      concat: {
        ...s.concat,
        enabled,
        target: enabled ? concatTarget(files) : s.concat.target,
        count: enabled ? media.length : 0,
      },
      imageMerge: enabled
        ? { ...s.imageMerge, enabled: false, job: null, count: 0 }
        : s.imageMerge,
    }));
  },

  setConcatTarget: (mime) =>
    set((s) => ({ concat: { ...s.concat, target: mime } })),

  runImageMerge: async () => {
    const { files, preset, running, ready: isReady, imageMerge } = get();
    if (running || !isReady || !imageMerge.enabled || !imageMerge.target) return;
    const images = files.filter((f) => f.domain === "raster");
    if (images.length < 2) return;
    let ids: string[];
    try {
      ids = await api().merge_images(
        images.map((f) => f.path),
        imageMerge.target,
        preset || null,
        {},
      );
    } catch (e) {
      set({ bootError: `启动图片合并失败：${e}` });
      return;
    }
    set({
      running: true,
      imageMerge: {
        ...imageMerge,
        job: {
          id: ids[0],
          name: "图片合并",
          count: images.length,
          state: "pending",
          progress: 0,
          step: "排队中",
        } as Job,
      },
    });
  },

  toggleImageMerge: (enabled) => {
    const files = get().files;
    const images = files.filter((f) => f.domain === "raster");
    if (enabled && images.length < 2) return;
    set((s) => ({
      imageMerge: {
        ...s.imageMerge,
        enabled,
        target: enabled ? imageMergeTarget(files) : s.imageMerge.target,
        count: enabled ? images.length : 0,
      },
      concat: enabled
        ? { ...s.concat, enabled: false, job: null, count: 0 }
        : s.concat,
    }));
  },

  setImageMergeTarget: (mime) =>
    set((s) => ({ imageMerge: { ...s.imageMerge, target: mime } })),

  runCue: async (cue, target) => {
    const { preset, running, ready: isReady } = get();
    if (running || !isReady) return;
    let ids: string[];
    try {
      ids = await api().split_cue(cue, target, null, preset || null, {});
    } catch (e) {
      set({ bootError: `启动 CUE 分轨失败：${e}` });
      return;
    }
    set({
      running: true,
      cueJob: {
        id: ids[0],
        name: cue.split(/[\\/]/).pop(),
        state: "pending",
        progress: 0,
        step: "排队中",
      } as Job,
    });
  },

  cancelJob: async (id) => {
    await api().cancel(id).catch(() => false);
  },

  applyJob: (job) => {
    let recent = get().recent;
    if (job.state === "done" && job.outPath) {
      const entry: RecentEntry = {
        id: job.id,
        name: job.name ?? "",
        outName: job.outName ?? "",
        outPath: job.outPath,
        time: Date.now(),
      };
      recent = [entry, ...recent.filter((r) => r.id !== entry.id)].slice(0, 6);
      saveRecent(recent);
    }
    set((s) => {
      const files = s.files.map((f) =>
        f.job && f.job.id === job.id ? { ...f, job: { ...f.job, ...job } } : f,
      );
      const concat =
        s.concat.job && s.concat.job.id === job.id
          ? { ...s.concat, job: { ...s.concat.job, ...job } }
          : s.concat;
      const imageMerge =
        s.imageMerge.job && s.imageMerge.job.id === job.id
          ? { ...s.imageMerge, job: { ...s.imageMerge.job, ...job } }
          : s.imageMerge;
      const cueJob =
        s.cueJob && s.cueJob.id === job.id
          ? { ...s.cueJob, ...job }
          : s.cueJob;
      const active =
        files.some(
          (f) =>
            f.job && (f.job.state === "running" || f.job.state === "pending"),
        ) ||
        (concat.job &&
          (concat.job.state === "running" || concat.job.state === "pending")) ||
        (imageMerge.job &&
          (imageMerge.job.state === "running" ||
            imageMerge.job.state === "pending")) ||
        (cueJob &&
          (cueJob.state === "running" || cueJob.state === "pending"));
      return { files, concat, imageMerge, cueJob, running: !!active, recent };
    });
  },

  setOutDir: (dir) =>
    set((s) => (s.env ? { env: { ...s.env, outDir: dir } } : {})),

  pickOutDir: async () => {
    const dir = await api().pick_out_dir();
    if (dir) get().setOutDir(dir);
  },

  resetOutDir: async () => {
    await api().clear_out_dir();
    get().setOutDir(null);
  },

  openEngines: (v) => set({ enginesOpen: v }),
  openHelp: (v) => {
    set({ helpOpen: v });
    if (v && !get().formats) void get().loadFormats();
  },

  clearRecent: () => {
    saveRecent([]);
    set({ recent: [] });
  },

  loadFormats: async () => {
    try {
      set({ formats: (await api().formats_data()) as FormatsGroup[] });
    } catch {
      set({ formats: [] });
    }
  },

  installEngine: async (key) => {
    const res = await api().install_engine(key);
    if (res.ok) {
      const env = await api().env();
      set((s) => ({
        env,
        engineProgress: { ...s.engineProgress, [key]: 1 },
      }));
    } else {
      set({ bootError: res.error ?? "引擎安装失败" });
    }
  },

  setEngineProgress: (key, pct) =>
    set((s) => ({ engineProgress: { ...s.engineProgress, [key]: pct } })),
}));

/** 派生统计：任务面板用 */
export function summary(files: FileRow[]) {
  let pending = 0;
  let done = 0;
  let failed = 0;
  let lossy = 0;
  let notChosen = 0;
  for (const f of files) {
    if (!f.chosen) notChosen++;
    if (!f.job || f.job.state === "pending") pending++;
    if (f.job?.state === "done") done++;
    if (f.job?.state === "failed") failed++;
    const t = targetOf(f, f.chosen);
    if (!f.job && t && isLossy(t.tier)) lossy++;
  }
  return { pending, done, failed, lossy, notChosen };
}
