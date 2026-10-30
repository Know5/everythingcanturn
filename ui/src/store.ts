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
  enginesOpen: boolean;
  helpOpen: boolean;
  formats: FormatsGroup[] | null;
  engineProgress: Record<string, number>;

  boot: () => Promise<void>;
  addPaths: (paths: string[]) => Promise<void>;
  removeFile: (path: string) => void;
  clearAll: () => Promise<void>;
  setDragOver: (v: boolean) => void;
  setPreset: (v: string) => void;
  openPicker: (target: string | null) => void;
  chooseTarget: (mime: string) => void;
  run: () => Promise<void>;
  cancelJob: (id: string) => Promise<void>;
  applyJob: (job: Job) => void;
  setOutDir: (dir: string | null) => void;
  pickOutDir: () => Promise<void>;
  resetOutDir: () => Promise<void>;
  openEngines: (v: boolean) => void;
  openHelp: (v: boolean) => void;
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
  enginesOpen: false,
  helpOpen: false,
  formats: null,
  engineProgress: {},

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
    if (!fresh.length) return;

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
    return void problems;
  },

  removeFile: (path) =>
    set((s) => ({ files: s.files.filter((f) => f.path !== path) })),

  clearAll: async () => {
    const active = get().files.filter(
      (f) => f.job && (f.job.state === "running" || f.job.state === "pending"),
    );
    await Promise.all(
      active.map((f) => api().cancel(f.job!.id).catch(() => false)),
    );
    set({ files: [], running: false });
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

  cancelJob: async (id) => {
    await api().cancel(id).catch(() => false);
  },

  applyJob: (job) =>
    set((s) => {
      const files = s.files.map((f) =>
        f.job && f.job.id === job.id ? { ...f, job: { ...f.job, ...job } } : f,
      );
      const active = files.some(
        (f) => f.job && (f.job.state === "running" || f.job.state === "pending"),
      );
      return { files, running: active };
    }),

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

/** 派生统计：底栏用 */
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
