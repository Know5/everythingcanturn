import { Collapse, Flex, Modal, Tag, Typography } from "antd";
import { useStore, type FormatsGroup } from "../store";
import { domainColor, radix } from "../theme";

const { Text } = Typography;

function FormatChips({ group }: { group: FormatsGroup }) {
  const color = domainColor[group.key] ?? radix.accent;
  return (
    <Flex wrap gap={5}>
      {group.items.map((i) => (
        <span
          key={i.mime}
          title={`${i.mime}\n${i.readonly ? "只读：能转入，不能转出" : "可读可写"}`}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 4,
            fontSize: 11,
            lineHeight: "19px",
            padding: "0 7px",
            borderRadius: 5,
            color: i.readonly ? radix.textDim : radix.text,
            background: i.readonly ? "rgba(255,255,255,0.03)" : `${color}14`,
            border: `1px solid ${i.readonly ? radix.border : `${color}2b`}`,
          }}
        >
          {i.label}
          <span style={{ fontSize: 9.5, color: radix.textFaint }}>.{i.ext}</span>
          {i.readonly && <span style={{ fontSize: 9.5, color: radix.warn }}>只读</span>}
        </span>
      ))}
    </Flex>
  );
}

export function HelpPanel() {
  const open = useStore((s) => s.helpOpen);
  const openHelp = useStore((s) => s.openHelp);
  const groups = useStore((s) => s.formats);

  const total = (groups ?? []).reduce((n, g) => n + g.count, 0);

  return (
    <Modal
      open={open}
      onCancel={() => openHelp(false)}
      footer={null}
      width={680}
      title="格式能力矩阵"
    >
      <Text type="secondary" style={{ fontSize: 12 }}>
        共 {total} 种格式。同域内可互转，跨域按下表单向可达。拖入文件后，列表只会显示该文件真正能转的目标。
      </Text>

      <Collapse
        ghost
        style={{ marginTop: 10 }}
        items={(groups ?? []).map((g) => ({
          key: g.key,
          label: (
            <Flex align="center" gap={8}>
              <Text style={{ fontSize: 12.5 }}>{g.label}</Text>
              <Text type="secondary" style={{ fontSize: 11 }}>
                {g.count} 种
              </Text>
            </Flex>
          ),
          children: (
            <Flex vertical gap={8}>
              <FormatChips group={g} />
            </Flex>
          ),
        }))}
      />

      <div
        style={{
          marginTop: 18,
          paddingTop: 14,
          borderTop: `1px solid ${radix.border}`,
        }}
      >
        <Text type="secondary" style={{ fontSize: 11, letterSpacing: 0.3 }}>
          怎么用
        </Text>
        <Flex vertical gap={7} style={{ marginTop: 8 }}>
          <Text style={{ fontSize: 12 }}>
            <b>1.</b> 把文件拖进窗口（或点击浏览）。列表会识别格式并给出可达目标。
          </Text>
          <Flex gap={6} wrap align="center">
            <Text style={{ fontSize: 12 }}>
              <b>2.</b> 点目标按钮挑格式。损失档位：
            </Text>
            <Tag color="green" bordered={false}>⚡ 免重编码</Tag>
            <Tag color="green" bordered={false}>✓ 无损</Tag>
            <Tag color="gold" bordered={false}>~ 有损</Tag>
            <Tag color="orange" bordered={false}>⚠ 丢结构</Tag>
            <Tag color="red" bordered={false}>⚠⚠ 需推断</Tag>
          </Flex>
          <Text style={{ fontSize: 12 }}>
            <b>3.</b> 批量时用「统一目标」一次设置；需要精细控制时换质量预设。
          </Text>
          <Text type="secondary" style={{ fontSize: 11.5 }}>
            命令行等价用法：convcore convert 文件 -t 目标 · convcore targets 文件 · convcore doctor
          </Text>
        </Flex>
      </div>
    </Modal>
  );
}
