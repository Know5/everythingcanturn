import { Tooltip } from "antd";
import {
  QuestionCircleOutlined,
  SafetyCertificateOutlined,
  ToolFilled,
  ThunderboltFilled,
} from "@ant-design/icons";
import { useStore } from "../store";
import { radix } from "../theme";

export function TopBar() {
  const env = useStore((s) => s.env);
  const openEngines = useStore((s) => s.openEngines);
  const openHelp = useStore((s) => s.openHelp);

  const engines = env?.engines ?? [];
  const missing = engines.filter((e) => !e.ok && e.tier === "needed");
  const ready = engines.filter((e) => e.ok).length;

  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark">
          <ThunderboltFilled />
        </div>
        <div className="brand-copy">
          <span className="brand-name">convcore</span>
          <span className="brand-sub">本地文件转换</span>
        </div>
      </div>

      <div className="topbar-spacer" />

      <div className="topbar-actions">
        <span
          className="status-pill"
          style={{ color: radix.textFaint, cursor: "default" }}
          title={`可读 ${env?.readable ?? 0} 种格式 · 可写 ${env?.writable ?? 0} 种`}
        >
          <SafetyCertificateOutlined style={{ fontSize: 12 }} />
          本地处理
        </span>

        <Tooltip
          title={
            missing.length
              ? `缺 ${missing.length} 个引擎：${missing.map((m) => m.key).join("、")}`
              : "所有已装引擎就绪"
          }
        >
          <button className="status-pill" onClick={() => openEngines(true)}>
            <span className={`status-dot${missing.length ? " warn" : ""}`} />
            <ToolFilled style={{ fontSize: 11 }} />
            {`引擎 ${ready}/${engines.length}`}
          </button>
        </Tooltip>

        <Tooltip title="格式能力与使用说明">
          <button className="icon-btn" onClick={() => openHelp(true)}>
            <QuestionCircleOutlined style={{ fontSize: 14 }} />
          </button>
        </Tooltip>
      </div>
    </header>
  );
}
