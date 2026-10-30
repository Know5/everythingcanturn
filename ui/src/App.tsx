/* 应用骨架：顶栏 / 队列工作区 / 右侧任务面板 / 三个弹层。
 *
 * 布局只在“有文件”时出现右侧面板；空状态占满整行，避免首屏出现一块
 * 全是 0 的统计面板。拖放事件由 bridge.ts 提供真实路径，这里只处理状态。
 */

import { useEffect } from "react";
import { App as AntApp, Alert, Popconfirm, Spin, Typography } from "antd";
import {
  ClearOutlined,
  PlusOutlined,
  SwapOutlined,
  InboxOutlined,
} from "@ant-design/icons";
import { bindDrop, bindJob, api } from "./bridge";
import { useStore } from "./store";
import { TopBar } from "./components/TopBar";
import { FileRow } from "./components/FileRow";
import { SidePanel } from "./components/SidePanel";
import { TargetPicker } from "./components/TargetPicker";
import { EnginePanel } from "./components/EnginePanel";
import { HelpPanel } from "./components/HelpPanel";
import { EmptyDrop } from "./components/bits";
import { RecentPanel } from "./components/RecentPanel";

const { Text } = Typography;

export default function App() {
  const { message } = AntApp.useApp();
  const boot = useStore((s) => s.boot);
  const addPaths = useStore((s) => s.addPaths);
  const files = useStore((s) => s.files);
  const ready = useStore((s) => s.ready);
  const bootError = useStore((s) => s.bootError);
  const dragOver = useStore((s) => s.dragOver);
  const setDragOver = useStore((s) => s.setDragOver);
  const adding = useStore((s) => s.adding);
  const openPicker = useStore((s) => s.openPicker);
  const setEngineProgress = useStore((s) => s.setEngineProgress);
  const applyJob = useStore((s) => s.applyJob);
  const clearAll = useStore((s) => s.clearAll);
  const run = useStore((s) => s.run);
  const runConcat = useStore((s) => s.runConcat);
  const runImageMerge = useStore((s) => s.runImageMerge);
  const concat = useStore((s) => s.concat);
  const imageMerge = useStore((s) => s.imageMerge);
  const running = useStore((s) => s.running);

  useEffect(() => {
    void boot();

    bindDrop(async (paths) => {
      const problems = await addPaths(paths);
      if (problems.length === 1) message.error(problems[0]);
      else if (problems.length > 1) {
        message.error(`${problems[0]}（另有 ${problems.length - 1} 个文件未添加）`);
      }
    });
    bindJob((job) => applyJob(job));
    window.onEngineProgress = (key, pct) => setEngineProgress(key, pct);

    const onDragOver = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("application/x-convcore-path")) return;
      e.preventDefault();
      setDragOver(true);
    };
    const onDragLeave = (e: DragEvent) => {
      if (!e.relatedTarget) setDragOver(false);
    };
    const onDrop = (e: DragEvent) => {
      e.preventDefault();
      setDragOver(false);
    };
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [boot, addPaths, applyJob, setDragOver, setEngineProgress, message]);

  const pick = async () => {
    try {
      const paths = await api().pick_files();
      if (!paths?.length) return;
      const problems = await addPaths(paths);
      if (problems.length === 1) message.error(problems[0]);
      else if (problems.length > 1) {
        message.error(`${problems[0]}（另有 ${problems.length - 1} 个文件未添加）`);
      }
    } catch (e) {
      message.error(`打开对话框失败：${e}`);
    }
  };

  const hasFiles = files.length > 0;
  const pending = files.filter((f) => !f.job || f.job.state === "pending").length;
  const notChosen = files.filter((f) => !f.chosen).length;
  const mediaCount = files.filter((f) => f.domain === "media").length;
  const imageCount = files.filter((f) => f.domain === "raster").length;
  const concatMode = concat.enabled && mediaCount >= 2;
  const imageMode = imageMerge.enabled && imageCount >= 2;
  const canRun = ready && !running && (
    imageMode
      ? !!imageMerge.target
      : concatMode
        ? !!concat.target
        : pending > 0 && notChosen === 0
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key.toLowerCase() === "o") {
        e.preventDefault();
        void pick();
      } else if (e.key === "Enter" && canRun) {
        e.preventDefault();
        void (imageMode ? runImageMerge() : concatMode ? runConcat() : run());
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pick, canRun, imageMode, concatMode, run, runImageMerge, runConcat]);

  return (
    <div className="app-shell">
      <TopBar />

      {!ready && !bootError && (
        <div className="boot-screen">
          <Spin />
          <Text type="secondary" style={{ fontSize: 11.5 }}>
            正在加载转换引擎…
          </Text>
        </div>
      )}

      {bootError && (
        <div style={{ padding: "22px 24px 0" }}>
          <Alert type="error" showIcon message="初始化失败" description={bootError} />
        </div>
      )}

      {ready && !hasFiles && (
        <div className="workspace empty">
          <div className="empty-stack">
            <EmptyDrop onClick={() => void pick()} hot={dragOver} />
            <RecentPanel />
          </div>
        </div>
      )}

      {ready && hasFiles && (
        <div className="workspace">
          <main className="queue-pane">
            <div className="queue-head">
              <div>
                <div className="eyebrow">转换工作区</div>
                <h1 className="queue-title">
                  转换队列 <span className="count">{files.length}</span>
                </h1>
                <p className="queue-sub">
                  选择目标格式后开始转换；所有文件只在本机处理。
                </p>
              </div>
              <div className="queue-actions">
                {adding && <Spin size="small" />}
                <button
                  className="head-action primary"
                  onClick={() => void pick()}
                  title="添加文件（Ctrl+O）"
                >
                  <PlusOutlined /> 添加文件
                </button>
                <button className="head-action" onClick={() => openPicker("*")}>
                  <SwapOutlined /> 统一目标
                </button>
                <Popconfirm
                  title="清空列表？"
                  description="进行中的任务会先被取消"
                  okText="清空"
                  cancelText="取消"
                  onConfirm={() => void clearAll()}
                >
                  <button className="head-action">
                    <ClearOutlined /> 清空
                  </button>
                </Popconfirm>
              </div>
            </div>

            <div className="file-list">
              {files.map((f) => (
                <FileRow key={f.path} file={f} />
              ))}
            </div>

            <button
              className="queue-more"
              onClick={() => void pick()}
              title="添加文件（Ctrl+O）"
            >
              <PlusOutlined />
              继续添加文件，或把文件拖进窗口
            </button>
          </main>

          <SidePanel />
        </div>
      )}

      {dragOver && (
        <div className="drop-overlay">
          <div className="drop-overlay-inner">
            <InboxOutlined style={{ fontSize: 24 }} />
            松开即可添加文件
          </div>
        </div>
      )}

      <TargetPicker />
      <EnginePanel />
      <HelpPanel />
    </div>
  );
}
