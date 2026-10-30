import { Button, Flex, Modal, Progress, Typography } from "antd";
import { api } from "../bridge";
import { useStore } from "../store";
import { radix } from "../theme";

const { Text } = Typography;

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
      width={600}
      title="外部引擎"
    >
      <Text type="secondary" style={{ fontSize: 12 }}>
        图片、PDF、文档、数据、压缩包、字体都不依赖外部引擎；只装你需要的。
      </Text>

      <div style={{ marginTop: 14 }}>
        {(env?.engines ?? []).map((e) => {
          const pct = progress[e.key];
          const installing = pct !== undefined && pct < 1;
          const color = e.ok ? radix.success : e.tier === "needed" ? radix.warn : radix.textFaint;
          return (
            <Flex
              key={e.key}
              align="flex-start"
              gap={10}
              style={{
                padding: "10px 0",
                borderTop: `1px solid ${radix.border}`,
              }}
            >
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 999,
                  marginTop: 7,
                  flex: "0 0 auto",
                  background: color,
                }}
              />
              <Flex vertical gap={2} style={{ flex: 1, minWidth: 0 }}>
                <Text style={{ fontSize: 12.5, fontWeight: 500 }}>{e.key}</Text>
                <Text type="secondary" style={{ fontSize: 11.5 }}>
                  {e.ok
                    ? e.version || "已就绪"
                    : `装了才能：${e.enables}${e.size ? ` · ${e.size}` : ""}`}
                </Text>
                {installing && (
                  <Progress
                    percent={Math.round(pct * 100)}
                    size="small"
                    strokeColor={radix.accent}
                    style={{ marginTop: 4, marginBottom: 0 }}
                  />
                )}
              </Flex>
              {!e.ok && e.key === "ffmpeg" && !installing && (
                <Button size="small" onClick={() => void install(e.key)}>
                  安装
                </Button>
              )}
              {!e.ok && e.key !== "ffmpeg" && (
                <Button size="small" type="text" onClick={() => openHint(e.hint)}>
                  怎么装
                </Button>
              )}
            </Flex>
          );
        })}
      </div>

      {unavailable.length > 0 && (
        <div style={{ marginTop: 18 }}>
          <Text type="secondary" style={{ fontSize: 11, letterSpacing: 0.3 }}>
            未启用的插件
          </Text>
          {unavailable.map(([name, why]) => (
            <Flex key={name} gap={10} align="flex-start" style={{ padding: "7px 0" }}>
              <span
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: 999,
                  marginTop: 7,
                  flex: "0 0 auto",
                  background: radix.textFaint,
                }}
              />
              <Flex vertical>
                <Text style={{ fontSize: 12 }}>{name}</Text>
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {why}
                </Text>
              </Flex>
            </Flex>
          ))}
        </div>
      )}
    </Modal>
  );
}
