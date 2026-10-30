import { useState } from "react";
import { Tooltip } from "antd";
import {
  CheckCircleFilled,
  CloseOutlined,
  DownOutlined,
  ExportOutlined,
  FolderOpenOutlined,
} from "@ant-design/icons";
import { api } from "../bridge";
import { formatDelta, formatSize } from "../format";
import { targetOf, useStore, type FileRow as Row } from "../store";
import { domainColor, radix } from "../theme";
import { TierMark } from "./bits";

export function FileRow({ file }: { file: Row }) {
  const openPicker = useStore((s) => s.openPicker);
  const removeFile = useStore((s) => s.removeFile);
  const cancelJob = useStore((s) => s.cancelJob);
  const moveFile = useStore((s) => s.moveFile);
  const [dragging, setDragging] = useState(false);
  const [dropTarget, setDropTarget] = useState(false);

  const job = file.job;
  const target = targetOf(file, file.chosen);
  const busy = job && (job.state === "running" || job.state === "pending");
  const done = job?.state === "done";
  const failed = job?.state === "failed";
  const color = domainColor[file.domain] ?? radix.accent;
  const ext = (file.name.split(".").pop() ?? "?").toUpperCase();

  return (
    <div
      className={`file-card${busy ? " is-running" : ""}${done ? " is-done" : ""}${
        dragging ? " is-dragging" : ""
      }${dropTarget ? " is-drop-target" : ""}`}
      draggable={!busy}
      onDragStart={(e) => {
        e.dataTransfer.setData("application/x-convcore-path", file.path);
        e.dataTransfer.effectAllowed = "move";
        setDragging(true);
      }}
      onDragEnd={() => {
        setDragging(false);
        setDropTarget(false);
      }}
      onDragOver={(e) => {
        if (!e.dataTransfer.types.includes("application/x-convcore-path")) return;
        e.preventDefault();
        e.stopPropagation();
        setDropTarget(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
          setDropTarget(false);
        }
      }}
      onDrop={(e) => {
        const from = e.dataTransfer.getData("application/x-convcore-path");
        if (from) {
          e.preventDefault();
          e.stopPropagation();
          moveFile(from, file.path);
        }
        setDropTarget(false);
      }}
    >
      <div
        className="file-icon"
        style={{
          color,
          background: `${color}18`,
          border: `1px solid ${color}2e`,
        }}
      >
        {file.thumb ? (
          <img src={file.thumb} alt="" />
        ) : ext.length > 4 ? (
          ext.slice(0, 4)
        ) : (
          ext
        )}
      </div>

      <div className="file-main">
        <Tooltip title={file.path} mouseEnterDelay={0.5} placement="topLeft">
          <div className="file-name">{file.name}</div>
        </Tooltip>
        {!done && (
          <div className="file-meta">
            {file.label}
            <span className="dot">·</span>
            {formatSize(file.size)}
            {file.detail && (
              <>
                <span className="dot">·</span>
                {file.detail}
              </>
            )}
          </div>
        )}

        {busy && (
          <>
            <div className="file-step">{job!.step || "排队中"}</div>
          </>
        )}

        {done && (
          <>
            <div className="file-result">
              <CheckCircleFilled />
              <span>{job!.outName || "转换完成"}</span>
              <span className="muted">
                {formatDelta(job!.srcSize, job!.outSize)}
                {job!.elapsed !== undefined ? ` · ${job!.elapsed}s` : ""}
                {job!.extra ? ` · 另 ${job!.extra} 个文件` : ""}
              </span>
            </div>
            {job!.pathDesc && (
              <div className="file-meta" style={{ color: radix.textFaint }}>
                {job!.pathDesc}
              </div>
            )}
          </>
        )}

        {failed && (
          <div className="file-meta" style={{ color: radix.dangerText }}>
            {job!.error ?? "转换失败"}
          </div>
        )}

        {job?.state === "cancelled" && (
          <div className="file-meta">已取消</div>
        )}

        {(job?.warnings ?? []).map((w, i) => (
          <div key={i} className="file-meta" style={{ color: radix.warn }}>
            ⚠ {w}
          </div>
        ))}
      </div>

      <div className="file-target">
        {!busy && !done && !failed && job?.state !== "cancelled" ? (
          <>
            <Tooltip title={target?.path} mouseEnterDelay={0.5}>
              <button
                className="target-pill"
                onClick={() => openPicker(file.path)}
              >
                {target ? (
                  <>
                    <span className="ext">→</span>
                    {target.label}
                    <span className="ext">.{target.ext}</span>
                  </>
                ) : (
                  "选择格式"
                )}
                <DownOutlined className="caret" />
              </button>
            </Tooltip>
            <TierMark target={target} />
          </>
        ) : busy ? (
          <>
            <span className="target-pill" style={{ cursor: "default" }}>
              {target?.label ?? "转换中"}
              {target && <span className="ext">.{target.ext}</span>}
            </span>
            <button
              className="head-action"
              style={{ height: 26, padding: "0 9px", fontSize: 10.5 }}
              onClick={() => job && void cancelJob(job.id)}
            >
              取消
            </button>
          </>
        ) : done ? (
          <>
            <TierMark target={target} showBetter={false} />
            <div style={{ display: "flex", gap: 4 }}>
              <Tooltip title="打开产物">
                <button
                  className="icon-btn"
                  onClick={() => void api().open_file(job!.outPath ?? "")}
                >
                  <ExportOutlined style={{ fontSize: 13 }} />
                </button>
              </Tooltip>
              <Tooltip title="在文件夹中定位">
                <button
                  className="icon-btn"
                  onClick={() => void api().reveal(job!.outPath ?? "")}
                >
                  <FolderOpenOutlined style={{ fontSize: 13 }} />
                </button>
              </Tooltip>
            </div>
          </>
        ) : (
          <TierMark target={target} showBetter={false} />
        )}

        {!busy && (
          <Tooltip title="从队列移除">
            <button className="file-remove" onClick={() => removeFile(file.path)}>
              <CloseOutlined style={{ fontSize: 11 }} />
            </button>
          </Tooltip>
        )}
      </div>

      {busy && (
        <div className="file-progress">
          <span style={{ width: `${Math.round((job!.progress ?? 0) * 100)}%` }} />
        </div>
      )}
    </div>
  );
}
