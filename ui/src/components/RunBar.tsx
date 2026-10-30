import { Button, Flex, Popconfirm, Typography } from "antd";
import { ThunderboltFilled } from "@ant-design/icons";
import { summary, useStore } from "../store";
import { radix } from "../theme";

const { Text } = Typography;

export function RunBar() {
  const files = useStore((s) => s.files);
  const running = useStore((s) => s.running);
  const ready = useStore((s) => s.ready);
  const run = useStore((s) => s.run);
  const clearAll = useStore((s) => s.clearAll);
  const openPicker = useStore((s) => s.openPicker);
  const env = useStore((s) => s.env);
  const resetOutDir = useStore((s) => s.resetOutDir);

  const { pending, done, failed, lossy, notChosen } = summary(files);
  const canRun = ready && !running && pending > 0 && notChosen === 0;

  return (
    <Flex
      align="center"
      gap={10}
      style={{
        height: 58,
        padding: "0 14px",
        borderTop: `1px solid ${radix.border}`,
        background: "rgba(17,17,19,0.86)",
        backdropFilter: "blur(10px)",
        flex: "0 0 auto",
      }}
    >
      <Flex vertical gap={1} style={{ minWidth: 0 }}>
        <Text type="secondary" style={{ fontSize: 11.5 }}>
          {pending} 个待转换
          {done > 0 && ` · ${done} 已完成`}
          {failed > 0 && <span style={{ color: radix.dangerText }}> · {failed} 失败</span>}
          {lossy > 0 && <span style={{ color: radix.warn }}> · {lossy} 项有信息损失</span>}
          {notChosen > 0 && (
            <span style={{ color: radix.warn }}> · {notChosen} 个未选目标</span>
          )}
        </Text>
        <Flex gap={6} align="center">
          <Text style={{ fontSize: 11, color: radix.textFaint }}>
            输出：{env?.outDir ?? "源文件所在目录"}
          </Text>
          {env?.outDir && (
            <Button type="link" size="small" onClick={() => void resetOutDir()} style={{ padding: 0, height: 14, fontSize: 11 }}>
              重置
            </Button>
          )}
        </Flex>
      </Flex>

      <div style={{ flex: 1 }} />

      {files.length > 0 && (
        <>
          <Button size="small" type="text" onClick={() => openPicker("*")}>
            统一目标
          </Button>
          <Popconfirm
            title="清空列表？"
            description="进行中的任务会先被取消"
            okText="清空"
            cancelText="取消"
            onConfirm={() => void clearAll()}
          >
            <Button size="small" type="text">
              清空
            </Button>
          </Popconfirm>
        </>
      )}

      <Button
        type="primary"
        icon={<ThunderboltFilled />}
        disabled={!canRun}
        onClick={() => void run()}
        style={{ height: 32, paddingInline: 16 }}
      >
        {running ? "转换中…" : pending > 0 ? `开始转换（${pending}）` : "开始转换"}
      </Button>
    </Flex>
  );
}
