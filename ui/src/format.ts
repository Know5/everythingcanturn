/* 展示层的小工具：字节、时长、路径、损失档位判定。 */

export function formatSize(n?: number): string {
  if (!n || n < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${i === 0 ? v : v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

export function formatDelta(src?: number, out?: number): string {
  if (!src || out === undefined || out === null) return "";
  const pct = ((out - src) / src) * 100;
  return `${formatSize(src)} → ${formatSize(out)}（${pct >= 0 ? "+" : ""}${pct.toFixed(0)}%）`;
}

export function fileName(p: string): string {
  return p.split(/[\\/]/).pop() || p;
}

export function dirName(p: string): string {
  const i = Math.max(p.lastIndexOf("\\"), p.lastIndexOf("/"));
  return i > 0 ? p.slice(0, i) : p;
}

/** 不可逆/有损档位：用于底栏"会有信息损失"的统计 */
export function isLossy(tier: string): boolean {
  return tier === "lossy" || tier === "structural" || tier === "inferred";
}
