/* 与 convcore/desktop/app.py 的返回结构一一对应的类型。
 *
 * 这些结构是冻结契约（见 docs/superpowers/specs/2026-10-29-desktop-ui-react-antd.md 第 5 节），
 * 后端改字段时这里必须同步，编译器会把前端用到的地方全部标红。
 */

export interface Env {
  readable: number;
  writable: number;
  plugins: string[];
  unavailable: Record<string, string>;
  domains: { key: string; label: string; count: number }[];
  engines: {
    key: string;
    ok: boolean;
    tier: string;
    enables: string;
    size: string;
    hint: string;
    version: string;
  }[];
  presets: { name: string; label: string; desc: string }[];
  outDir: string | null;
}

export interface TargetItem {
  mime: string;
  label: string;
  ext: string;
  /** copy | lossless | lossy | structural | inferred */
  tier: string;
  tierLabel: string;
  /** 实际路径优于静态声明（如免重编码） */
  better: boolean;
  reversible: boolean;
  path: string;
}

export interface TargetGroup {
  key: string;
  label: string;
  items: TargetItem[];
}

export interface FileInfo {
  path: string;
  name: string;
  size: number;
  mime: string;
  label: string;
  domain: string;
  domainLabel: string;
  detail: string;
  groups: TargetGroup[];
  gatedHint: string | null;
  default: string | null;
}

export type JobState = "pending" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  name?: string;
  state: JobState;
  progress?: number;
  step?: string;
  outPath?: string;
  outName?: string;
  srcSize?: number;
  outSize?: number;
  elapsed?: number;
  extra?: number;
  warnings?: string[];
  error?: string;
  pathDesc?: string;
  better?: boolean;
}

/** pywebview 注入的 API（window.pywebview.api）。 */
export interface PywebviewApi {
  env(): Promise<Env>;
  formats_data(): Promise<unknown[]>;
  pick_files(): Promise<string[]>;
  pick_out_dir(): Promise<string | null>;
  clear_out_dir(): Promise<void>;
  inspect(path: string): Promise<FileInfo & { error?: string }>;
  convert(
    items: { path: string; target: string }[],
    preset: string | null,
    opts: Record<string, unknown>,
  ): Promise<string[]>;
  cancel(jid: string): Promise<boolean>;
  cancel_all(): Promise<number>;
  reveal(path: string): Promise<boolean>;
  open_file(path: string): Promise<boolean>;
  open_url(url: string): Promise<boolean>;
  install_engine(key: string): Promise<{ ok: boolean; version?: string; error?: string }>;
}
