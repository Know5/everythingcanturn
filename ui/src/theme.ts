/* 设计基线：取自 Radix Colors 的暗色阶梯（slate / blue / green / amber / orange / red）。
 *
 * 为什么用 Radix 而不是自己调：它的暗色阶是按"同一感知亮度、不同色相"构造的，
 * 1–2 是背景层、3–5 是组件面、6–8 是描边、9–10 是实心色、11–12 是文字。
 * 直接引用这套数值，能保证任意两个面之间都有可读的对比度，也避免"手调出一堆灰"。
 */

export const radix = {
  bg0: "#0b0c0e", // 窗口底，比 slate-1 再暗半档
  bg1: "#111113", // slate-1  应用背景
  surface: "#18191b", // slate-2 卡片/列表底
  surfaceHi: "#212225", // slate-3 悬停/次级面
  surfaceActive: "#272a2d", // slate-4 选中
  border: "#2e3135", // slate-5 分隔线
  borderStrong: "#43484e", // slate-7 强调描边
  textFaint: "#696e77", // slate-9
  textDim: "#b0b4ba", // slate-11
  text: "#edeef0", // slate-12
  accent: "#0090ff", // blue-9
  accentHover: "#3b9eff", // blue-10
  accentText: "#70b8ff", // blue-11
  success: "#30a46c", // green-9
  successText: "#3dd68c",
  warn: "#ffc53d", // amber-9
  warnText: "#ffd60a",
  danger: "#e5484d", // red-9
  dangerText: "#ec5d5e",
  orange: "#f76b15", // orange-9
  violet: "#6e56cf",
  cyan: "#00a2c7",
} as const;

/** 领域色（用于扩展名徽标，取各色相的 9 号实心色） */
export const domainColor: Record<string, string> = {
  raster: radix.accent,
  media: radix.violet,
  document: radix.warn,
  markup: radix.success,
  table: radix.cyan,
  archive: radix.textDim,
  font: "#e93d82",
  vector: radix.orange,
  tree: "#30a46c",
};

/** 损失档位色：绿→黄→橙→红，语义与 README 的档位一致 */
export const tierColor: Record<string, string> = {
  copy: radix.success,
  lossless: radix.success,
  lossy: radix.warn,
  structural: radix.orange,
  inferred: radix.danger,
};

export const tierIcon: Record<string, string> = {
  copy: "⚡",
  lossless: "✓",
  lossy: "~",
  structural: "⚠",
  inferred: "⚠⚠",
};

const fontFamily =
  '-apple-system, "Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI", "PingFang SC", Roboto, sans-serif';
const monoFamily = '"Cascadia Code", "JetBrains Mono", Consolas, "SF Mono", monospace';

import { theme, type ThemeConfig } from "antd";

export const appTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: radix.accent,
    colorBgBase: radix.bg1,
    colorBgContainer: radix.surface,
    colorBgElevated: radix.surfaceHi,
    colorBgSpotlight: radix.surfaceHi,
    colorBorder: radix.border,
    colorBorderSecondary: "#24272a",
    colorText: radix.text,
    colorTextSecondary: radix.textDim,
    colorTextTertiary: radix.textFaint,
    colorSuccess: radix.success,
    colorWarning: radix.warn,
    colorError: radix.danger,
    colorLink: radix.accentText,
    colorFillSecondary: "rgba(255,255,255,0.06)",
    borderRadius: 7,
    borderRadiusLG: 10,
    controlHeight: 32,
    fontSize: 13,
    fontFamily,
    fontFamilyCode: monoFamily,
    motionDurationMid: "0.14s",
    wireframe: false,
  },
  components: {
    Button: { primaryShadow: "none", defaultShadow: "none", fontWeight: 500 },
    Card: { colorBgContainer: radix.surface },
    Modal: { contentBg: radix.surface, headerBg: "transparent", titleFontSize: 14 },
    Input: { colorBgContainer: radix.bg1 },
    Select: { optionSelectedBg: "rgba(0,144,255,0.16)" },
    Progress: { defaultColor: radix.accent, remainingColor: "rgba(255,255,255,0.07)" },
    Tooltip: { colorBgSpotlight: "#2b2f33" },
    Tag: { defaultBg: radix.surfaceHi, defaultColor: radix.textDim },
    Divider: { colorSplit: radix.border },
    Segmented: { itemSelectedBg: radix.surfaceActive },
  },
};
