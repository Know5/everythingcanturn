/* 设计 token：深色桌面工具。层级靠“近黑背景 → 抬升面板 → 卡片”三档明度，
 * 强调色只用于主操作和选中态，避免整屏彩色噪点。
 *
 * 字体统一走系统 UI：Windows 上 Segoe UI Variable，中文回退微软雅黑；
 * 不引外部字体，保证离线与启动速度。
 */

export const radix = {
  bg0: "#08090b",
  bg1: "#0b0d10",
  surface: "#111419",
  surfaceHi: "#161a20",
  surfaceActive: "#1c222b",
  border: "#222833",
  borderStrong: "#333c4a",
  textFaint: "#6c7686",
  textDim: "#9ba4b2",
  text: "#eef1f5",
  accent: "#4c8dff",
  accentHover: "#6aa1ff",
  accentText: "#8ab6ff",
  accentSoft: "rgba(76, 141, 255, 0.12)",
  success: "#35d399",
  successText: "#6ee7b7",
  warn: "#f2c14e",
  warnText: "#f7d070",
  danger: "#ff6b6b",
  dangerText: "#ff8d8d",
  orange: "#f08a4b",
  violet: "#8b7bff",
  cyan: "#38c7d6",
  shadow: "0 18px 48px rgba(0, 0, 0, 0.42)",
  shadowSoft: "0 8px 24px rgba(0, 0, 0, 0.28)",
  glass: "rgba(17, 20, 25, 0.78)",
} as const;

/** 领域色（扩展名徽标与格式分组） */
export const domainColor: Record<string, string> = {
  raster: radix.accent,
  media: radix.violet,
  document: radix.warn,
  markup: radix.success,
  table: radix.cyan,
  archive: radix.textDim,
  font: "#e879b9",
  vector: radix.orange,
  tree: "#4ade80",
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
  '"Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI", "PingFang SC", "Noto Sans SC", sans-serif';
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
    colorBorderSecondary: "#1b212a",
    colorText: radix.text,
    colorTextSecondary: radix.textDim,
    colorTextTertiary: radix.textFaint,
    colorTextQuaternary: "#5b6472",
    colorSuccess: radix.success,
    colorWarning: radix.warn,
    colorError: radix.danger,
    colorLink: radix.accentText,
    colorFillSecondary: "rgba(255,255,255,0.055)",
    colorFillTertiary: "rgba(255,255,255,0.035)",
    borderRadius: 9,
    borderRadiusLG: 14,
    borderRadiusSM: 7,
    controlHeight: 34,
    fontSize: 13,
    fontFamily,
    fontFamilyCode: monoFamily,
    motionDurationMid: "0.16s",
    wireframe: false,
  },
  components: {
    Button: {
      primaryShadow: "none",
      defaultShadow: "none",
      fontWeight: 500,
      paddingInline: 14,
    },
    Card: { colorBgContainer: radix.surface },
    Modal: {
      contentBg: radix.surface,
      headerBg: "transparent",
      titleFontSize: 16,
      titleColor: radix.text,
    },
    Input: { colorBgContainer: radix.bg1, activeBorderColor: radix.accent },
    Select: {
      optionSelectedBg: radix.accentSoft,
      optionActiveBg: "rgba(255,255,255,0.05)",
    },
    Progress: {
      defaultColor: radix.accent,
      remainingColor: "rgba(255,255,255,0.06)",
    },
    Tooltip: { colorBgSpotlight: "#252b35" },
    Tag: { defaultBg: radix.surfaceHi, defaultColor: radix.textDim },
    Divider: { colorSplit: radix.border },
    Segmented: { itemSelectedBg: radix.surfaceActive },
    Empty: { colorTextDescription: radix.textFaint },
  },
};
