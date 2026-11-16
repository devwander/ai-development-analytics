import { TASK_TYPE_LABEL } from "@/lib/format";

/**
 * Horizontal categorical bars with direct labels and a table view.
 *
 * Horizontal because the category names are long words, not dates. Direct
 * labels are mandatory here rather than optional: three slots of the light
 * palette sit below 3:1 against the surface, and the relief rule says a chart
 * using them must carry visible labels or a table - this carries both.
 */

const SERIES = [
  "var(--series-1)",
  "var(--series-2)",
  "var(--series-3)",
  "var(--series-4)",
  "var(--series-5)",
  "var(--series-6)",
  "var(--series-7)",
  "var(--series-8)",
];

interface Props {
  counts: Record<string, number>;
  total: number;
  emptyMessage: string;
}

export function CategoryBars({ counts, total, emptyMessage }: Props) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);

  if (entries.length === 0) {
    return <p className="empty">{emptyMessage}</p>;
  }

  // Past eight slots the tail folds into "Other" - a generated ninth hue is
  // indistinguishable from an existing one under colour-vision deficiency.
  const head = entries.slice(0, 8);
  const tail = entries.slice(8);
  const rows =
    tail.length > 0
      ? [...head, ["other", tail.reduce((sum, [, n]) => sum + n, 0)] as [string, number]]
      : head;

  const max = Math.max(...rows.map(([, value]) => value));

  return (
    <>
      <div style={{ display: "grid", gap: 8 }}>
        {rows.map(([key, value], index) => (
          <div
            key={key}
            style={{ display: "grid", gridTemplateColumns: "130px 1fr 56px", gap: 10, alignItems: "center" }}
          >
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              {TASK_TYPE_LABEL[key] ?? key}
            </span>
            <div style={{ height: 14, position: "relative" }}>
              <div
                style={{
                  width: `${(value / max) * 100}%`,
                  height: "100%",
                  minWidth: 3,
                  background: SERIES[index % SERIES.length],
                  borderRadius: "0 4px 4px 0",
                }}
              />
            </div>
            <span
              style={{
                fontSize: 12,
                color: "var(--text-primary)",
                textAlign: "right",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {value}
              <span className="muted" style={{ marginLeft: 5 }}>
                {total > 0 ? `${Math.round((value / total) * 100)}%` : ""}
              </span>
            </span>
          </div>
        ))}
      </div>

      <details className="table-view">
        <summary>Ver como tabela</summary>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Categoria</th>
                <th className="num">Sessões</th>
                <th className="num">Participação</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([key, value]) => (
                <tr key={key}>
                  <td>{TASK_TYPE_LABEL[key] ?? key}</td>
                  <td className="num">{value}</td>
                  <td className="num">
                    {total > 0 ? `${((value / total) * 100).toFixed(1)}%` : "n/d"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </>
  );
}
