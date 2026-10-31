/* 复用的小视觉组件：损失档位标记、空状态拖放区。 */

import { InboxOutlined, PlusOutlined } from "@ant-design/icons";
import { radix, tierColor } from "../theme";
import type { TargetItem } from "../types";

export function TierMark({
  target,
  showBetter = true,
}: {
  target: TargetItem | null;
  showBetter?: boolean;
}) {
  if (!target) return null;
  const color = tierColor[target.tier] ?? radix.textDim;
  return (
    <span className="tier-badge" style={{ color }}>
      <span className="swatch" />
      {target.tierLabel}
      {showBetter && target.better ? " · 免重编码" : ""}
    </span>
  );
}

const FORMATS = ["PNG", "JPG", "WEBP", "PDF", "DOCX", "MD", "MP4", "MP3", "SVG", "ZIP"];

export function EmptyDrop({
  onClick,
  hot,
  onCue,
}: {
  onClick: () => void;
  hot: boolean;
  onCue?: () => void;
}) {
  return (
    <div
      className={`empty-drop${hot ? " hot" : ""}`}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onClick();
      }}
    >
      <div className="empty-icon">
        <InboxOutlined />
      </div>
      <h2 className="empty-title">把文件拖到这里</h2>
      <p className="empty-sub">
        图片、文档、音视频与压缩包。自动识别格式，只列出真正可达的目标；
        多张图片或多段音视频可在队列里合并成一个输出。
      </p>
      <div className="empty-actions">
        <button
          className="empty-cta"
          onClick={(e) => {
            e.stopPropagation();
            onClick();
          }}
        >
          <PlusOutlined /> 选择文件
        </button>
        {onCue && (
          <button
            className="empty-cta subtle"
            onClick={(e) => {
              e.stopPropagation();
              onCue();
            }}
          >
            CUE 分轨
          </button>
        )}
      </div>
      <div className="format-chips">
        {FORMATS.map((f) => (
          <span key={f} className="format-chip">
            {f}
          </span>
        ))}
      </div>
      <div className="empty-foot">支持 60+ 种格式 · 文件只在本机处理</div>
    </div>
  );
}
