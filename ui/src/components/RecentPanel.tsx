import { App as AntApp } from "antd";
import {
  CheckCircleFilled,
  ExportOutlined,
  FolderOpenOutlined,
  PlusOutlined,
} from "@ant-design/icons";
import { api } from "../bridge";
import { useStore } from "../store";

export function RecentPanel() {
  const { message } = AntApp.useApp();
  const recent = useStore((s) => s.recent);
  const addPaths = useStore((s) => s.addPaths);
  const clearRecent = useStore((s) => s.clearRecent);

  if (!recent.length) return null;

  const open = async (path: string) => {
    if (!(await api().open_file(path))) {
      message.error("文件已不存在，或系统无法打开");
    }
  };

  const reveal = async (path: string) => {
    if (!(await api().reveal(path))) {
      message.error("文件已不存在，无法定位");
    }
  };

  const readd = async (path: string) => {
    const problems = await addPaths([path]);
    if (problems.length) message.error(problems[0]);
  };

  return (
    <div className="recent-panel">
      <div className="recent-head">
        <span className="panel-title">最近完成</span>
        <button className="recent-clear" onClick={clearRecent}>
          清除记录
        </button>
      </div>
      {recent.map((r) => (
        <div key={r.id} className="recent-row">
          <CheckCircleFilled style={{ color: "var(--success)", fontSize: 12 }} />
          <div className="recent-main">
            <div className="recent-name">{r.outName || r.name}</div>
            <div className="recent-meta">
              {r.name}
              <span className="dot">·</span>
              {new Date(r.time).toLocaleString("zh-CN", {
                month: "numeric",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </div>
          </div>
          <div className="recent-actions">
            <button className="recent-action" onClick={() => void open(r.outPath)}>
              <ExportOutlined /> 打开
            </button>
            <button className="recent-action" onClick={() => void reveal(r.outPath)}>
              <FolderOpenOutlined /> 定位
            </button>
            <button className="recent-action" onClick={() => void readd(r.outPath)}>
              <PlusOutlined /> 再加入
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
