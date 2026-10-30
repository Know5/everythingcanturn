/* 真实界面：顶栏 / 文件列表 / 底栏 + 三个弹层。
 *
 * 与 P0 spike 的区别：数据全部来自 Python 侧（inspect / convert / onJob 事件）。
 * 拖放依赖 bridge.ts 的全局保护与应用级事件注册，见该文件头注释。
 */

import { useEffect } from "react";
import { App as AntApp, Alert, Button, Flex, Spin, Typography } from "antd";
import { bindDrop, bindJob, api } from "./bridge";
import { useStore } from "./store";
import { TopBar } from "./components/TopBar";
import { FileRow } from "./components/FileRow";
import { RunBar } from "./components/RunBar";
import { TargetPicker } from "./components/TargetPicker";
import { EnginePanel } from "./components/EnginePanel";
import { HelpPanel } from "./components/HelpPanel";
import { EmptyDrop } from "./components/bits";
import { radix } from "./theme";

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

  useEffect(() => {
    void boot();

    bindDrop(async (paths) => {
      await addPaths(paths);
    });
    bindJob((job) => applyJob(job));
    window.onEngineProgress = (key, pct) => setEngineProgress(key, pct);

    const onDragOver = (e: DragEvent) => {
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
  }, [boot, addPaths, applyJob, setDragOver, setEngineProgress]);

  const pick = async () => {
    try {
      const paths = await api().pick_files();
      if (paths?.length) await addPaths(paths);
    } catch (e) {
      message.error(`打开对话框失败：${e}`);
    }
  };

  return (
    <Flex vertical style={{ height: "100%", position: "relative", overflow: "hidden" }}>
      {/* 顶部一层很淡的光晕，避免大面积纯黑发闷 */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          pointerEvents: "none",
          background:
            "radial-gradient(700px 240px at 50% -90px, rgba(0,144,255,0.10), transparent 72%)",
        }}
      />

      <TopBar />

      {files.length > 0 && (
        <Flex
          align="center"
          gap={8}
          style={{
            padding: "6px 14px",
            borderBottom: `1px solid ${radix.border}`,
            flex: "0 0 auto",
          }}
        >
          <Text type="secondary" style={{ fontSize: 11.5 }}>
            {files.length} 个文件
          </Text>
          <div style={{ flex: 1 }} />
          {adding && <Spin size="small" />}
          <Button type="text" size="small" onClick={() => openPicker("*")}>
            统一目标…
          </Button>
        </Flex>
      )}

      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflow: "auto",
          display: "flex",
          flexDirection: "column",
          position: "relative",
          zIndex: 1,
          outline: dragOver ? `1px dashed ${radix.accent}` : "1px dashed transparent",
          outlineOffset: -6,
          transition: "outline-color .12s",
          background: dragOver ? "rgba(0,144,255,0.045)" : "transparent",
        }}
      >
        {!ready && !bootError && (
          <Flex align="center" justify="center" style={{ flex: 1 }}>
            <Spin />
          </Flex>
        )}
        {bootError && (
          <div style={{ padding: 16 }}>
            <Alert type="error" showIcon message="初始化失败" description={bootError} />
          </div>
        )}
        {ready && files.length === 0 && (
          <div style={{ flex: 1, minHeight: 0 }}>
            <EmptyDrop onClick={() => void pick()} />
          </div>
        )}
        {files.map((f) => (
          <FileRow key={f.path} file={f} />
        ))}
      </div>

      <RunBar />
      <TargetPicker />
      <EnginePanel />
      <HelpPanel />
    </Flex>
  );
}
