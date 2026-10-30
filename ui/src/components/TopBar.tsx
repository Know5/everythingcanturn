import { Button, Flex, Select, Tag, Tooltip, Typography } from "antd";
import {
  FolderOpenOutlined,
  QuestionCircleOutlined,
  ToolOutlined,
  ThunderboltFilled,
} from "@ant-design/icons";
import { useStore } from "../store";
import { radix } from "../theme";

const { Text } = Typography;

export function TopBar() {
  const env = useStore((s) => s.env);
  const preset = useStore((s) => s.preset);
  const setPreset = useStore((s) => s.setPreset);
  const openEngines = useStore((s) => s.openEngines);
  const openHelp = useStore((s) => s.openHelp);
  const pickOutDir = useStore((s) => s.pickOutDir);

  const missing = env?.engines.filter((e) => !e.ok && e.tier === "needed") ?? [];

  return (
    <Flex
      align="center"
      gap={8}
      style={{
        height: 50,
        padding: "0 14px",
        borderBottom: `1px solid ${radix.border}`,
        flex: "0 0 auto",
        WebkitAppRegion: "drag",
      } as React.CSSProperties}
    >
      <div
        style={{
          width: 24,
          height: 24,
          borderRadius: 7,
          background: `linear-gradient(135deg, ${radix.accent}, ${radix.violet})`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flex: "0 0 auto",
        }}
      >
        <ThunderboltFilled style={{ color: "#fff", fontSize: 12 }} />
      </div>
      <Text strong style={{ fontSize: 13.5, letterSpacing: 0.2 }}>
        convcore
      </Text>
      {env && (
        <Text type="secondary" style={{ fontSize: 11.5 }}>
          可读 {env.readable} · 可写 {env.writable}
        </Text>
      )}

      <div style={{ flex: 1 }} />

      <Select
        size="small"
        variant="borderless"
        value={preset}
        onChange={setPreset}
        placeholder="默认"
        style={{ width: 104 }}
        options={[
          { value: "", label: "默认" },
          ...(env?.presets ?? []).map((p) => ({ value: p.name, label: p.label })),
        ]}
      />
      <Tooltip title={env?.outDir ? `输出到 ${env.outDir}` : "输出到源文件所在目录"}>
        <Button
          type="text"
          size="small"
          icon={<FolderOpenOutlined />}
          onClick={() => void pickOutDir()}
          style={{ color: env?.outDir ? radix.accentText : undefined }}
        >
          输出目录
        </Button>
      </Tooltip>
      <Tooltip
        title={
          missing.length
            ? `缺 ${missing.length} 个引擎：${missing.map((m) => m.key).join("、")}`
            : "所有已装引擎就绪"
        }
      >
        <Button
          type="text"
          size="small"
          icon={<ToolOutlined />}
          onClick={() => openEngines(true)}
        >
          引擎
          <span
            style={{
              display: "inline-block",
              width: 6,
              height: 6,
              borderRadius: 999,
              marginLeft: 4,
              background: missing.length ? radix.warn : radix.success,
            }}
          />
        </Button>
      </Tooltip>
      <Button
        type="text"
        size="small"
        icon={<QuestionCircleOutlined />}
        onClick={() => openHelp(true)}
      />
      {env?.outDir && (
        <Tag
          style={{ margin: 0, fontSize: 10.5, maxWidth: 160 }}
          color="processing"
          bordered={false}
        >
          自定义目录
        </Tag>
      )}
    </Flex>
  );
}
