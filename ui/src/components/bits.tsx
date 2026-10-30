/* 复用的小视觉组件：扩展名徽标、损失档位标签、空状态。 */

import { Flex, Typography } from "antd";
import { domainColor, radix, tierColor, tierIcon } from "../theme";
import type { TargetItem } from "../types";

const { Text } = Typography;

export function ExtBadge({ ext, domain }: { ext: string; domain: string }) {
  const color = domainColor[domain] ?? radix.accent;
  const label = (ext || "?").toUpperCase();
  return (
    <div
      style={{
        width: 34,
        height: 34,
        borderRadius: 8,
        flex: "0 0 auto",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: label.length > 4 ? 8.5 : 10,
        fontWeight: 700,
        letterSpacing: 0.2,
        color,
        background: `${color}1a`,
        border: `1px solid ${color}33`,
        userSelect: "none",
      }}
    >
      {label}
    </div>
  );
}

export function TierTag({
  target,
  showBetter = true,
}: {
  target: TargetItem | null;
  showBetter?: boolean;
}) {
  if (!target) return null;
  const color = tierColor[target.tier] ?? radix.textDim;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        fontSize: 11,
        lineHeight: "18px",
        height: 20,
        padding: "0 7px",
        borderRadius: 999,
        color,
        background: `${color}17`,
        border: `1px solid ${color}2e`,
        whiteSpace: "nowrap",
        userSelect: "none",
      }}
    >
      <span style={{ opacity: 0.9 }}>{tierIcon[target.tier]}</span>
      {target.tierLabel}
      {showBetter && target.better && (
        <span style={{ color: radix.success }}>· 免重编码</span>
      )}
    </span>
  );
}

export function EmptyDrop({ onClick }: { onClick: () => void }) {
  return (
    <Flex
      vertical
      align="center"
      justify="center"
      gap={10}
      onClick={onClick}
      style={{ height: "100%", cursor: "pointer", userSelect: "none" }}
    >
      <div
        style={{
          width: 52,
          height: 52,
          borderRadius: 14,
          border: `1.5px dashed ${radix.borderStrong}`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 20,
          color: radix.textDim,
        }}
      >
        ↓
      </div>
      <Text style={{ color: radix.textDim, fontSize: 13 }}>把文件拖进窗口</Text>
      <Text type="secondary" style={{ fontSize: 12 }}>
        或点击这里浏览 · 自动识别格式并列出可转目标
      </Text>
    </Flex>
  );
}
