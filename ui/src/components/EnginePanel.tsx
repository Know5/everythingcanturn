import { Button, Modal, Progress } from "antd";
import { api } from "../bridge";
import { useStore } from "../store";
import { radix } from "../theme";

export function EnginePanel() {
  const open = useStore((s) => s.enginesOpen);
  const openEngines = useStore((s) => s.openEngines);
  const env = useStore((s) => s.env);
  const progress = useStore((s) => s.engineProgress);
  const install = useStore((s) => s.installEngine);

  const unavailable = Object.entries(env?.unavailable ?? {});

  const openHint = (hint: string) => {
    const url = (hint.match(/https?:\/\/\S+/) ?? [])[0];
    if (url) void api().open_url(url.replace(/[)。，]$/, ""));
  };

  return (
    <Modal
      open={open}
      onCancel={() => openEngines(false)}
      footer={null}
      width={640}
      className="panel-modal"
      title="引擎与依赖"
    >
      <div className="soft-note">
        图片、PDF、文档、数据、压缩包与字体都不依赖外部引擎。按需安装，装完立即生效。
      </div>

      <div className="soft-list">
        {(env?.engines ?? []).map((e) => {
          const pct = progress[e.key];
          const installing = pct !== undefined && pct < 1;
          const color = e.ok
            ? radix.success
            : e.tier === "needed"
              ? radix.warn
              : radix.textFaint;
          return (
            <div key={e.key} className="soft-row">
              <span className="soft-dot" style={{ background: color }} />
              <div className="soft-main">
                <div className="soft-title" style={{ textTransform: "capitalize" }}>
                  {e.key}
                </div>
                <div className="soft-note">
                  {e.ok
                    ? e.version || "已就绪"
                    : `装了才能：${e.enables}${e.size ? ` · ${e.size}` : ""}`}
                </div>
                {installing && (
                  <Progress
                    percent={Math.round(pct * 100)}
                    size="small"
                    strokeColor={radix.accent}
                    style={{ marginTop: 6, marginBottom: 0 }}
                  />
                )}
              </div>
              {!e.ok && e.key === "ffmpeg" && !installing && (
                <Button size="small" type="primary" onClick={() => void install(e.key)}>
                  安装
                </Button>
              )}
              {!e.ok && e.key !== "ffmpeg" && (
                <Button size="small" type="text" onClick={() => openHint(e.hint)}>
                  如何安装
                </Button>
              )}
            </div>
          );
        })}
      </div>

      {unavailable.length > 0 && (
        <div style={{ marginTop: 20 }}>
          <div className="section-label">未启用的插件</div>
          <div className="soft-list">
            {unavailable.map(([name, why]) => (
              <div key={name} className="soft-row">
                <span className="soft-dot" style={{ background: radix.textFaint }} />
                <div className="soft-main">
                  <div className="soft-title">{name}</div>
                  <div className="soft-note">{why}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}
