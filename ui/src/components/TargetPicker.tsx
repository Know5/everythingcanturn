import { useMemo, useState } from "react";
import { Button, Empty, Flex, Input, Modal, Typography } from "antd";
import { targetOf, useStore } from "../store";
import { domainColor, radix, tierColor, tierIcon } from "../theme";

const { Text } = Typography;

/** 目标格式选择器。分组来自 inspect 的 groups（同域优先、域内按保真度排好序）。
 *  批量模式（picker.target === '*'）只列出全部文件都支持、或至少一个文件支持的目标。 */
export function TargetPicker() {
  const { target, files, chooseTarget, openPicker } = useStore((s) => ({
    target: s.picker.target,
    files: s.files,
    chooseTarget: s.chooseTarget,
    openPicker: s.openPicker,
  }));
  const [query, setQuery] = useState("");

  const open = target !== null;
  const isBulk = target === "*";
  const current = isBulk ? null : files.find((f) => f.path === target) ?? null;

  // 批量模式用所有文件的分组合并去重；单文件模式用该文件的
  const groups = useMemo(() => {
    if (!open) return [];
    const byKey = new Map<
      string,
      { key: string; label: string; items: Map<string, (typeof files)[number]["groups"][number]["items"][number]> }
    >();
    const sources = isBulk ? files : current ? [current] : [];
    for (const f of sources) {
      for (const g of f.groups) {
        if (!byKey.has(g.key)) byKey.set(g.key, { key: g.key, label: g.label, items: new Map() });
        const bucket = byKey.get(g.key)!;
        for (const item of g.items) {
          // 批量模式下排除"这个文件转成自己"的无意义项在所有文件都成立的情况
          if (!bucket.items.has(item.mime)) bucket.items.set(item.mime, item);
        }
      }
    }
    const q = query.trim().toLowerCase();
    return [...byKey.values()]
      .map((g) => ({
        ...g,
        items: [...g.items.values()].filter(
          (i) =>
            !q ||
            i.label.toLowerCase().includes(q) ||
            i.ext.toLowerCase().includes(q) ||
            i.mime.toLowerCase().includes(q),
        ),
      }))
      .filter((g) => g.items.length);
  }, [open, isBulk, files, current, query]);

  const bulkCount = (mime: string) =>
    files.filter((f) => targetOf(f, mime)).length;

  return (
    <Modal
      open={open}
      onCancel={() => openPicker(null)}
      footer={null}
      width={600}
      destroyOnClose
      title={isBulk ? "统一目标格式" : "选择目标格式"}
      styles={{ body: { paddingTop: 4 } }}
    >
      <Text type="secondary" style={{ fontSize: 12 }}>
        {isBulk
          ? `只对支持该目标的文件生效，其余保持原选择（共 ${files.length} 个文件）`
          : `${current?.name ?? ""} · 同域目标排最前，只包含这个文件真正能走的路径`}
      </Text>

      <Input.Search
        placeholder="搜索格式、扩展名或 MIME…"
        allowClear
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        style={{ margin: "12px 0 2px" }}
      />

      <div style={{ maxHeight: 400, overflow: "auto", paddingRight: 4 }}>
        {groups.length === 0 && (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的格式" />
        )}
        {groups.map((g) => {
          const color = domainColor[g.key] ?? radix.accent;
          return (
            <div key={g.key} style={{ marginTop: 14 }}>
              <Flex align="center" gap={6} style={{ marginBottom: 7 }}>
                <span
                  style={{
                    width: 3,
                    height: 11,
                    borderRadius: 2,
                    background: color,
                    display: "inline-block",
                  }}
                />
                <Text type="secondary" style={{ fontSize: 11, letterSpacing: 0.3 }}>
                  {g.label}
                </Text>
              </Flex>
              <Flex wrap gap={6}>
                {g.items.map((item) => {
                  const c = tierColor[item.tier] ?? radix.textDim;
                  const chosen = !isBulk && current?.chosen === item.mime;
                  return (
                    <Button
                      key={item.mime}
                      size="small"
                      type={chosen ? "primary" : "default"}
                      ghost={chosen}
                      onClick={() => chooseTarget(item.mime)}
                      title={item.path}
                      style={{ paddingInline: 9 }}
                    >
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                        {item.label}
                        <span style={{ fontSize: 10, color: chosen ? undefined : c }}>
                          {tierIcon[item.tier]}
                        </span>
                        {isBulk && (
                          <span style={{ fontSize: 10, opacity: 0.6 }}>
                            {bulkCount(item.mime)}
                          </span>
                        )}
                      </span>
                    </Button>
                  );
                })}
              </Flex>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
