import { Tooltip } from "antd";
import {
  BorderOutlined,
  CloseOutlined,
  MinusOutlined,
  QuestionCircleOutlined,
  SafetyCertificateOutlined,
  ToolFilled,
} from "@ant-design/icons";
import iconUrl from "../assets/icon.png";
import { api } from "../bridge";
import { useStore } from "../store";

export function TopBar() {
  const env = useStore((s) => s.env);
  const openEngines = useStore((s) => s.openEngines);
  const openHelp = useStore((s) => s.openHelp);

  const engines = env?.engines ?? [];
  const missing = engines.filter((e) => !e.ok && e.tier === "needed");
  const ready = engines.filter((e) => e.ok).length;

  const toggleMaximize = () => {
    void api().window_toggle_maximize();
  };

  return (
    <header className="topbar">
      <div
        className="brand pywebview-drag-region"
        onDoubleClick={toggleMaximize}
      >
        <div className="brand-mark">
          <img src={iconUrl} alt="" />
        </div>
        <div className="brand-copy">
          <span className="brand-name">convcore</span>
          <span className="brand-sub">本地文件转换</span>
        </div>
      </div>

      <div
        className="titlebar-drag pywebview-drag-region"
        onDoubleClick={toggleMaximize}
      />

      <div className="topbar-actions">
        <span
          className="status-pill local-pill"
          style={{ cursor: "default" }}
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

        <div className="window-divider" />

        <div className="window-actions">
          <button
            className="window-btn"
            title="最小化"
            onClick={() => void api().window_minimize()}
          >
            <MinusOutlined />
          </button>
          <button
            className="window-btn"
            title="最大化 / 还原"
            onClick={toggleMaximize}
          >
            <BorderOutlined />
          </button>
          <button
            className="window-btn close"
            title="关闭"
            onClick={() => void api().window_close()}
          >
            <CloseOutlined />
          </button>
        </div>
      </div>
    </header>
  );
}
