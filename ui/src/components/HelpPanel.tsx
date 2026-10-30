import { Collapse, Modal, Tag } from "antd";
import { useStore, type FormatsGroup } from "../store";
import { domainColor, radix } from "../theme";

function FormatChips({ group }: { group: FormatsGroup }) {
  const color = domainColor[group.key] ?? radix.accent;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
      {group.items.map((i) => (
        <span
          key={i.mime}
          title={`${i.mime}\n${i.readonly ? "只读：能转入，不能转出" : "可读可写"}`}
          className="format-chip"
          style={{
            color: i.readonly ? radix.textDim : radix.text,
            background: i.readonly ? "rgba(255,255,255,0.025)" : `${color}14`,
            borderColor: i.readonly ? radix.border : `${color}2b`,
          }}
        >
          {i.label}
          <span style={{ color: radix.textFaint, marginLeft: 4 }}>.{i.ext}</span>
          {i.readonly && <span style={{ color: radix.warn, marginLeft: 5 }}>只读</span>}
        </span>
      ))}
    </div>
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
      width={700}
      className="panel-modal"
      title="格式与用法"
    >
      <div className="soft-note">
        共 {total} 种格式。同域内可互转，跨域按下表单向可达。拖入文件后，列表只显示该文件真正能转的目标。
      </div>

      <Collapse
        ghost
        style={{ marginTop: 10 }}
        items={(groups ?? []).map((g) => ({
          key: g.key,
          label: (
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 12.5, color: radix.text }}>{g.label}</span>
              <span style={{ fontSize: 11, color: radix.textFaint }}>
                {g.count} 种
              </span>
            </div>
          ),
          children: <FormatChips group={g} />,
        }))}
      />

      <div
        style={{
          marginTop: 18,
          paddingTop: 14,
          borderTop: `1px solid ${radix.border}`,
        }}
      >
        <div className="section-label">怎么用</div>
        <div className="soft-list" style={{ marginTop: 8 }}>
          <div className="soft-row">
            <span className="soft-dot" style={{ background: radix.accent }} />
            <div className="soft-note">
              把文件拖进窗口，或点击“选择文件”。列表会识别格式并给出可达目标。
            </div>
          </div>
          <div className="soft-row">
            <span className="soft-dot" style={{ background: radix.success }} />
            <div className="soft-note">
              点行内目标按钮挑格式。损失档位：
              <Tag color="green" bordered={false}>⚡ 免重编码</Tag>
              <Tag color="green" bordered={false}>✓ 无损</Tag>
              <Tag color="gold" bordered={false}>~ 有损</Tag>
              <Tag color="orange" bordered={false}>⚠ 丢结构</Tag>
              <Tag color="red" bordered={false}>⚠⚠ 需推断</Tag>
            </div>
          </div>
          <div className="soft-row">
            <span className="soft-dot" style={{ background: radix.violet }} />
            <div className="soft-note">
              批量时用“统一目标”一次设置；需要精细控制时在右侧切换质量预设。
            </div>
          </div>
          <div className="soft-row">
            <span className="soft-dot" style={{ background: radix.textFaint }} />
            <div className="soft-note">
              命令行等价用法：convcore convert 文件 -t 目标 · convcore targets 文件 · convcore doctor
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
}
