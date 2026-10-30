import { Button, Flex, Progress, Typography } from "antd";
import { CheckCircleFilled } from "@ant-design/icons";
import { api } from "../bridge";
import { formatDelta, formatSize } from "../format";
import { targetOf, useStore, type FileRow as Row } from "../store";
import { radix } from "../theme";
import { ExtBadge, TierTag } from "./bits";
import { useState } from "react";

const { Text } = Typography;

export function FileRow({ file }: { file: Row }) {
  const openPicker = useStore((s) => s.openPicker);
  const removeFile = useStore((s) => s.removeFile);
  const cancelJob = useStore((s) => s.cancelJob);
  const [hover, setHover] = useState(false);

  const job = file.job;
  const target = targetOf(file, file.chosen);
  const busy = job && (job.state === "running" || job.state === "pending");

  return (
    <div
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        padding: "9px 14px",
        borderBottom: `1px solid ${radix.border}`,
        background: hover ? "rgba(255,255,255,0.018)" : "transparent",
        transition: "background .12s",
      }}
    >
      <Flex gap={11} align="flex-start">
        <ExtBadge ext={file.domain ? file.name.split(".").pop() ?? "" : "?"} domain={file.domain} />

        <div style={{ flex: 1, minWidth: 0 }}>
          <Flex gap={7} align="center">
            <Text
              strong
              ellipsis={{ tooltip: file.path }}
              style={{ fontSize: 13, maxWidth: 460 }}
            >
              {file.name}
            </Text>
            {job?.state === "done" && (
              <CheckCircleFilled style={{ color: radix.success, fontSize: 11 }} />
            )}
          </Flex>

          <Text
            type="secondary"
            style={{ fontSize: 11.5, display: "block", marginTop: 1 }}
          >
            {[file.label, formatSize(file.size), file.detail].filter(Boolean).join(" · ")}
          </Text>

          {busy && (
            <div style={{ marginTop: 6, maxWidth: 440 }}>
              <Progress
                percent={Math.round((job!.progress ?? 0) * 100)}
                size="small"
                showInfo={false}
                strokeColor={radix.accent}
                trailColor="rgba(255,255,255,0.07)"
                style={{ margin: 0 }}
              />
              <Text type="secondary" style={{ fontSize: 11 }}>
                {job!.step || "排队中"}
              </Text>
            </div>
          )}

          {job?.state === "done" && (
            <Flex gap={8} align="center" wrap style={{ marginTop: 2 }}>
              <Text style={{ fontSize: 12, color: radix.successText }}>
                → {job.outName}
              </Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                {formatDelta(job.srcSize, job.outSize)}
                {job.elapsed !== undefined ? ` · ${job.elapsed}s` : ""}
                {job.extra ? ` · 另 ${job.extra} 个文件` : ""}
              </Text>
            </Flex>
          )}

          {job?.state === "cancelled" && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              已取消
            </Text>
          )}

          {job?.state === "failed" && (
            <Text style={{ fontSize: 12, color: radix.dangerText }}>
              ✗ {job.error ?? "转换失败"}
            </Text>
          )}

          {(job?.warnings ?? []).length > 0 && (
            <div style={{ marginTop: 3 }}>
              {job!.warnings!.map((w, i) => (
                <Text
                  key={i}
                  style={{ fontSize: 11, color: radix.warn, display: "block" }}
                >
                  ⚠ {w}
                </Text>
              ))}
            </div>
          )}

          {job?.pathDesc && job.state === "done" && (
            <Text type="secondary" style={{ fontSize: 10.5, display: "block" }}>
              {job.pathDesc}
            </Text>
          )}
        </div>

        <Flex gap={5} align="center" style={{ flex: "0 0 auto", paddingTop: 1 }}>
          {!job || job.state === "pending" ? (
            <>
              <Button size="small" onClick={() => openPicker(file.path)}>
                {target ? target.label : "选择格式"} ▾
              </Button>
              <TierTag target={target} />
              <Button type="text" size="small" onClick={() => removeFile(file.path)}>
                移除
              </Button>
            </>
          ) : job.state === "running" ? (
            <Button type="text" size="small" onClick={() => void cancelJob(job.id)}>
              取消
            </Button>
          ) : job.state === "done" ? (
            <>
              <TierTag target={target} showBetter={false} />
              <Button
                type="text"
                size="small"
                onClick={() => api().open_file(job.outPath ?? "")}
                disabled={!job.outPath}
              >
                打开
              </Button>
              <Button
                type="text"
                size="small"
                onClick={() => api().reveal(job.outPath ?? "")}
                disabled={!job.outPath}
              >
                定位
              </Button>
            </>
          ) : (
            <>
              <TierTag target={target} showBetter={false} />
              <Button type="text" size="small" onClick={() => removeFile(file.path)}>
                移除
              </Button>
            </>
          )}
        </Flex>
      </Flex>
    </div>
  );
}
