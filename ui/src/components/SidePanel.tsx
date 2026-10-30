import { Progress, Select, Switch, Tooltip } from "antd";
import {
  CheckCircleOutlined,
  DownOutlined,
  FolderOpenOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltFilled,
  WarningOutlined,
} from "@ant-design/icons";
import { formatSize } from "../format";
import { summary, targetOf, useStore } from "../store";
import { radix } from "../theme";

export function SidePanel() {
  const files = useStore((s) => s.files);
  const env = useStore((s) => s.env);
  const preset = useStore((s) => s.preset);
  const setPreset = useStore((s) => s.setPreset);
  const running = useStore((s) => s.running);
  const ready = useStore((s) => s.ready);
  const run = useStore((s) => s.run);
  const runConcat = useStore((s) => s.runConcat);
  const concat = useStore((s) => s.concat);
  const toggleConcat = useStore((s) => s.toggleConcat);
  const setConcatTarget = useStore((s) => s.setConcatTarget);
  const imageMerge = useStore((s) => s.imageMerge);
  const runImageMerge = useStore((s) => s.runImageMerge);
  const toggleImageMerge = useStore((s) => s.toggleImageMerge);
  const setImageMergeTarget = useStore((s) => s.setImageMergeTarget);
  const pickOutDir = useStore((s) => s.pickOutDir);
  const resetOutDir = useStore((s) => s.resetOutDir);

  const { pending, done, failed, lossy, notChosen } = summary(files);
  const total = files.reduce((n, f) => n + f.size, 0);
  const mediaFiles = files.filter((f) => f.domain === "media");
  const imageFiles = files.filter((f) => f.domain === "raster");
  const concatMode = concat.enabled && mediaFiles.length >= 2;
  const imageMode = imageMerge.enabled && imageFiles.length >= 2;
  const canRun = ready && !running && (
    imageMode
      ? !!imageMerge.target
      : concatMode
        ? !!concat.target
        : pending > 0 && notChosen === 0
  );
  const concatOptions = mediaFiles[0]?.groups.find((g) => g.key === "media")?.items ?? [];
  const concatItem = concatOptions.find((i) => i.mime === concat.target);
  const imageOptions = (imageFiles[0]?.groups.find((g) => g.key === "raster")?.items ?? [])
    .filter((i) => ["image/gif", "image/webp", "image/tiff"].includes(i.mime));
  const imageItem = imageOptions.find((i) => i.mime === imageMerge.target);

  const outputs = new Map<string, { label: string; ext: string; count: number }>();
  if (imageMode && imageItem) {
    outputs.set(imageItem.mime, {
      label: imageItem.label,
      ext: imageItem.ext,
      count: 1,
    });
  } else if (concatMode && concatItem) {
    outputs.set(concatItem.mime, {
      label: concatItem.label,
      ext: concatItem.ext,
      count: 1,
    });
  }
  for (const f of files) {
    if (concatMode || imageMode) break;
    const t = targetOf(f, f.chosen);
    if (!t) continue;
    const row = outputs.get(t.mime);
    if (row) row.count++;
    else outputs.set(t.mime, { label: t.label, ext: t.ext, count: 1 });
  }

  return (
    <aside className="side-panel">
      <div className="side-scroll">
        <div className="panel-block">
          <div className="panel-title">任务概览</div>
          <div className="stat-hero">
            <b>{files.length}</b>
            <span>个文件 · {formatSize(total)}</span>
          </div>
          <div className="stat-grid">
            <div className="stat-cell">
              <b>{pending}</b>
              <span>待转换</span>
            </div>
            <div className="stat-cell">
              <b>{done}</b>
              <span>已完成</span>
            </div>
            <div className={`stat-cell${lossy ? " warn" : ""}`}>
              <b>{lossy}</b>
              <span>信息损失</span>
            </div>
          </div>
          {failed > 0 && (
            <div className="total-size" style={{ color: radix.dangerText }}>
              {failed} 个文件转换失败
            </div>
          )}
          <div
            className={`loss-note${
              lossy === 0 && notChosen === 0 ? " neutral" : ""
            }`}
          >
            {lossy === 0 && notChosen === 0 ? (
              <CheckCircleOutlined style={{ marginTop: 1 }} />
            ) : (
              <WarningOutlined style={{ marginTop: 1 }} />
            )}
            <span>
              {notChosen > 0
                ? `${notChosen} 个文件还没有选择目标格式`
                : lossy > 0
                  ? `${lossy} 项转换会丢失信息，可在目标格式里选择更保真的选项`
                  : "当前目标均为无损或免重编码"}
            </span>
          </div>
        </div>

        <div className="panel-divider" />

        <div className="panel-block">
          <div className="panel-title">输出设置</div>
          <div style={{ fontSize: 10.5, color: radix.textFaint, marginBottom: -2 }}>
            质量预设
          </div>
          <Select
            value={preset}
            onChange={setPreset}
            style={{ width: "100%" }}
            options={[
              { value: "", label: "默认（按格式自动）" },
              ...(env?.presets ?? []).map((p) => ({
                value: p.name,
                label: p.label,
              })),
            ]}
          />
          <Tooltip title={env?.outDir ?? "输出到源文件所在目录"}>
            <button className="out-path" onClick={() => void pickOutDir()}>
              <FolderOpenOutlined style={{ fontSize: 13 }} />
              <span className="path">{env?.outDir ?? "源文件所在目录"}</span>
              <DownOutlined className="chevron" />
            </button>
          </Tooltip>
          {env?.outDir && (
            <button
              className="head-action"
              style={{ width: "100%", justifyContent: "center" }}
              onClick={() => void resetOutDir()}
            >
              <ReloadOutlined /> 恢复默认目录
            </button>
          )}
        </div>

        <div className="panel-divider" />

        {imageFiles.length >= 2 && (
          <div className="panel-block">
            <div className="panel-title">图片序列</div>
            <div className="concat-toggle">
              <div>
                <b>合成动图</b>
                <span>{imageFiles.length} 张图片按队列顺序</span>
              </div>
              <Switch
                checked={imageMerge.enabled}
                onChange={(v) => toggleImageMerge(v)}
                size="small"
              />
            </div>
            {imageMerge.enabled && (
              <>
                <Select
                  value={imageMerge.target ?? undefined}
                  onChange={setImageMergeTarget}
                  style={{ width: "100%" }}
                  options={imageOptions.map((i) => ({
                    value: i.mime,
                    label: `${i.label} .${i.ext}`,
                  }))}
                  placeholder="选择动图格式"
                />
                <div className="soft-note" style={{ marginTop: 6 }}>
                  默认每帧 100ms；GIF/WebP/TIFF 支持动画。
                </div>
              </>
            )}
            {imageMerge.job && (
              <div className="concat-status">
                <div className="concat-status-head">
                  <span>{imageMerge.job.step || "合成中"}</span>
                  <span>{imageMerge.job.count ?? imageFiles.length} 张</span>
                </div>
                <Progress
                  percent={Math.round((imageMerge.job.progress ?? 0) * 100)}
                  size="small"
                  showInfo={false}
                />
                {imageMerge.job.error && (
                  <div className="concat-error">{imageMerge.job.error}</div>
                )}
                {imageMerge.job.outPath && (
                  <div className="soft-note">{imageMerge.job.outPath}</div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="panel-divider" />

        {mediaFiles.length >= 2 && (
          <div className="panel-block">
            <div className="panel-title">音视频合并</div>
            <div className="concat-toggle">
              <div>
                <b>按队列顺序拼接</b>
                <span>{mediaFiles.length} 个音视频文件</span>
              </div>
              <Switch
                checked={concat.enabled}
                onChange={(v) => toggleConcat(v)}
                size="small"
              />
            </div>
            {concat.enabled && (
              <>
                <Select
                  value={concat.target ?? undefined}
                  onChange={setConcatTarget}
                  style={{ width: "100%" }}
                  options={concatOptions.map((i) => ({
                    value: i.mime,
                    label: `${i.label} .${i.ext}`,
                  }))}
                  placeholder="选择合并后的格式"
                />
                <div className="soft-note" style={{ marginTop: 6 }}>
                  同编码直接拼接；编码/分辨率不一致时自动统一转码。
                </div>
              </>
            )}
            {concat.job && (
              <div className="concat-status">
                <div className="concat-status-head">
                  <span>{concat.job.step || "合并中"}</span>
                  <span>{concat.job.count ?? mediaFiles.length} 个文件</span>
                </div>
                <Progress
                  percent={Math.round((concat.job.progress ?? 0) * 100)}
                  size="small"
                  showInfo={false}
                />
                {concat.job.error && (
                  <div className="concat-error">{concat.job.error}</div>
                )}
                {concat.job.outPath && (
                  <div className="soft-note">{concat.job.outPath}</div>
                )}
              </div>
            )}
          </div>
        )}

        <div className="panel-divider" />

        <div className="panel-block">
          <div className="panel-title">本次输出</div>
          <div className="output-list">
            {outputs.size === 0 && (
              <span className="soft-note">尚未选择目标格式</span>
            )}
            {[...outputs.values()].map((o) => (
              <div key={o.ext} className="output-row">
                <span className="output-fmt">
                  {o.label}
                  <span className="ext">.{o.ext}</span>
                </span>
                <span className="output-count">× {o.count}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="side-footer">
        <button
          className="primary-cta"
          disabled={!canRun}
          onClick={() =>
            void (imageMode ? runImageMerge() : concatMode ? runConcat() : run())
          }
          title={
            imageMode || concatMode ? "开始合并" : "开始转换（Ctrl+Enter）"
          }
        >
          <ThunderboltFilled />
          {running
            ? imageMode
              ? "合成中…"
              : concatMode
                ? "合并中…"
                : "转换中…"
            : imageMode
              ? `合成动图 · ${imageFiles.length}`
              : concatMode
                ? `合并音视频 · ${mediaFiles.length}`
                : pending > 0
                  ? `开始转换 · ${pending}`
                  : "开始转换"}
        </button>
        <div className="privacy-note">
          <SafetyCertificateOutlined />
          文件只在本机处理，不会上传
        </div>
      </div>
    </aside>
  );
}
