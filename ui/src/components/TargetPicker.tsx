import { useEffect, useMemo, useState } from "react";
import { Empty, Input, Modal } from "antd";
import { targetOf, useStore } from "../store";
import { domainColor, radix, tierColor, tierIcon } from "../theme";

/** 目标格式选择器：搜索 + 按域分组 + 档位标记。
 *  批量模式只列出至少一个文件支持的目标，并显示覆盖数量。 */
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

  useEffect(() => {
    if (open) setQuery("");
  }, [open, target]);

  const groups = useMemo(() => {
    if (!open) return [];
    const byKey = new Map<
      string,
      {
        key: string;
        label: string;
        items: Map<
          string,
          (typeof files)[number]["groups"][number]["items"][number]
        >;
      }
    >();
    const sources = isBulk ? files : current ? [current] : [];
    for (const f of sources) {
      for (const g of f.groups) {
        if (!byKey.has(g.key)) {
          byKey.set(g.key, { key: g.key, label: g.label, items: new Map() });
        }
        const bucket = byKey.get(g.key)!;
        for (const item of g.items) {
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

  return (
    <Modal
      open={open}
      onCancel={() => openPicker(null)}
      footer={null}
      width={680}
      destroyOnClose
      className="target-modal"
      title={isBulk ? "统一目标格式" : "选择目标格式"}
    >
      <div style={{ fontSize: 11.5, color: radix.textFaint, lineHeight: 1.6 }}>
        {isBulk
          ? `只对支持该目标的文件生效，其余保持原选择（共 ${files.length} 个文件）`
          : `${current?.name ?? ""} · 同域目标排最前，只包含这个文件真正能走的路径`}
      </div>

      <Input.Search
        className="target-search"
        placeholder="搜索格式、扩展名或 MIME…"
        allowClear
        autoFocus
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />

      <div className="target-scan">
        {groups.length === 0 && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="没有匹配的格式"
            style={{ padding: "30px 0" }}
          />
        )}
        {groups.map((g) => {
          const color = domainColor[g.key] ?? radix.accent;
          return (
            <div key={g.key} className="target-group">
              <div className="target-group-head">
                <span className="bar" style={{ background: color }} />
                <span className="label">{g.label}</span>
              </div>
              <div className="target-grid">
                {g.items.map((item) => {
                  const c = tierColor[item.tier] ?? radix.textDim;
                  const chosen = !isBulk && current?.chosen === item.mime;
                  return (
                    <button
                      key={item.mime}
                      className={`target-option${chosen ? " selected" : ""}`}
                      onClick={() => chooseTarget(item.mime)}
                      title={item.path}
                    >
                      <span className="fmt">
                        <b>{item.label}</b>
                        <small>.{item.ext}</small>
                      </span>
                      <span className="side">
                        <span className="tier" style={{ color: c }}>
                          {tierIcon[item.tier]} {item.tierLabel}
                        </span>
                        {isBulk && (
                          <span className="bulk">
                            {files.filter((f) => targetOf(f, item.mime)).length} 个文件
                          </span>
                        )}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      <div className="target-legend">
        {Object.entries(tierColor).map(([tier, color]) => (
          <span key={tier} className="item">
            <span className="dot" style={{ background: color }} />
            {tierIcon[tier]}{" "}
            {tier === "copy"
              ? "免重编码"
              : tier === "lossless"
                ? "无损"
                : tier === "lossy"
                  ? "有损"
                  : tier === "structural"
                    ? "丢结构"
                    : "需推断"}
          </span>
        ))}
      </div>
    </Modal>
  );
}
