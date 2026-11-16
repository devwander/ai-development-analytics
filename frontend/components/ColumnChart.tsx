"use client";

import { useId, useState } from "react";

import { formatCost, formatNumber, formatTokens } from "@/lib/format";

/**
 * Single-series column chart.
 *
 * Deliberate constraints, each from the chart method:
 *  - one series, so no legend - the card title says what is plotted;
 *  - one y-scale only. Two measures of different scale get two charts, never a
 *    second axis;
 *  - columns capped at 24px with a 2px surface gap, rounded only at the data
 *    end and square at the baseline;
 *  - exactly one direct label (the maximum). A number on every column is noise;
 *  - hover tooltip per column, with a hit target spanning the full band so it
 *    stays usable when a column is 1px tall.
 */

export interface ColumnDatum {
  key: string;
  label: string;
  value: number;
  tooltip: string;
  /**
   * The value could not be determined, as opposed to being zero. Drawn as a
   * hollow marker at the baseline: a null rendered as a zero-height bar is
   * indistinguishable from a real zero, which is exactly the confusion the rest
   * of the app works to avoid.
   */
  missing?: boolean;
}

/**
 * A serialisable descriptor rather than a function: this is a Client Component,
 * and a Server Component cannot hand it a formatter callback.
 */
export type ValueFormat = "number" | "cost" | "tokens";

const FORMATTERS: Record<ValueFormat, (value: number) => string> = {
  number: (value) => formatNumber(Math.round(value)),
  cost: (value) => formatCost(value),
  tokens: (value) => formatTokens(Math.round(value)),
};

/**
 * Axis ticks round to clean numbers - cents on a gridline are noise nobody
 * reads. The direct label and the tooltip keep full precision.
 */
const TICK_FORMATTERS: Record<ValueFormat, (value: number) => string> = {
  number: (value) => formatNumber(Math.round(value)),
  cost: (value) => (value === 0 ? "0" : `US$ ${Math.round(value).toLocaleString("pt-BR")}`),
  tokens: (value) => formatTokens(Math.round(value)),
};

interface Props {
  data: ColumnDatum[];
  format: ValueFormat;
  height?: number;
  emptyMessage?: string;
}

const PAD = { top: 18, right: 8, bottom: 26, left: 46 };
const MAX_BAR = 24;
const GAP = 2;
const RADIUS = 4;

/** Rounded at the top, square at the baseline. */
function columnPath(x: number, y: number, width: number, height: number): string {
  const r = Math.min(RADIUS, width / 2, height);
  if (height <= 0) return "";
  return [
    `M${x},${y + height}`,
    `L${x},${y + r}`,
    `Q${x},${y} ${x + r},${y}`,
    `L${x + width - r},${y}`,
    `Q${x + width},${y} ${x + width},${y + r}`,
    `L${x + width},${y + height}`,
    "Z",
  ].join(" ");
}

/** Clean tick values - 0, then rounded steps. */
function ticksFor(max: number): number[] {
  if (max <= 0) return [0];
  const rough = max / 3;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? magnitude * 10;
  const ticks: number[] = [];
  for (let value = 0; value <= max + step * 0.001; value += step) ticks.push(value);
  return ticks;
}

export function ColumnChart({
  data,
  format,
  height = 200,
  emptyMessage = "sem dados no período",
}: Props) {
  const [hovered, setHovered] = useState<number | null>(null);
  const clipId = useId();
  const formatValue = FORMATTERS[format];
  const formatTick = TICK_FORMATTERS[format];

  if (data.length === 0) {
    return <p className="empty">{emptyMessage}</p>;
  }

  const width = 720;
  const plotWidth = width - PAD.left - PAD.right;
  const plotHeight = height - PAD.top - PAD.bottom;
  const max = Math.max(...data.filter((d) => !d.missing).map((d) => d.value), 0);
  const ticks = ticksFor(max);
  const scaleMax = ticks[ticks.length - 1] || 1;

  const band = plotWidth / data.length;
  const barWidth = Math.max(1, Math.min(MAX_BAR, band - GAP));
  const measured = data.filter((d) => !d.missing);
  const maxIndex = data.reduce(
    (best, d, i) => (!d.missing && d.value > data[best].value ? i : best),
    data.findIndex((d) => !d.missing),
  );

  // Label every nth tick so the axis never collides with itself.
  const labelEvery = Math.ceil(data.length / 12);

  return (
    <div style={{ position: "relative" }}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        height={height}
        role="img"
        aria-label="gráfico de colunas"
        style={{ display: "block", overflow: "visible" }}
      >
        <defs>
          <clipPath id={clipId}>
            <rect x={PAD.left} y={PAD.top - 8} width={plotWidth} height={plotHeight + 8} />
          </clipPath>
        </defs>

        {ticks.map((tick) => {
          const y = PAD.top + plotHeight - (tick / scaleMax) * plotHeight;
          return (
            <g key={tick}>
              <line
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y}
                y2={y}
                stroke={tick === 0 ? "var(--axis)" : "var(--grid)"}
                strokeWidth={1}
              />
              <text
                x={PAD.left - 8}
                y={y + 4}
                textAnchor="end"
                fontSize={11}
                fill="var(--text-muted)"
                style={{ fontVariantNumeric: "tabular-nums" }}
              >
                {formatTick(tick)}
              </text>
            </g>
          );
        })}

        <g clipPath={`url(#${clipId})`}>
          {data.map((datum, index) => {
            const x = PAD.left + index * band + (band - barWidth) / 2;
            const isHovered = hovered === index;

            if (datum.missing) {
              // A hollow outline at the baseline: present, measured, unknown.
              const height = 6;
              return (
                <rect
                  key={datum.key}
                  x={x}
                  y={PAD.top + plotHeight - height}
                  width={barWidth}
                  height={height}
                  rx={2}
                  fill="none"
                  stroke={isHovered ? "var(--text-secondary)" : "var(--axis)"}
                  strokeWidth={1.5}
                  strokeDasharray="3 2"
                />
              );
            }

            const barHeight = scaleMax > 0 ? (datum.value / scaleMax) * plotHeight : 0;
            const y = PAD.top + plotHeight - barHeight;
            return (
              <path
                key={datum.key}
                d={columnPath(x, y, barWidth, Math.max(barHeight, datum.value > 0 ? 2 : 0))}
                fill={isHovered ? "var(--seq-600)" : "var(--seq-450)"}
              />
            );
          })}
        </g>

        {/* One direct label: the maximum. */}
        {max > 0 &&
          maxIndex >= 0 &&
          (() => {
            const barHeight = (data[maxIndex].value / scaleMax) * plotHeight;
            const x = PAD.left + maxIndex * band + band / 2;
            const y = PAD.top + plotHeight - barHeight - 6;
            return (
              <text
                x={x}
                y={y}
                textAnchor="middle"
                fontSize={11}
                fontWeight={600}
                fill="var(--text-secondary)"
              >
                {formatValue(data[maxIndex].value)}
              </text>
            );
          })()}

        {data.map((datum, index) =>
          index % labelEvery === 0 ? (
            <text
              key={`x-${datum.key}`}
              x={PAD.left + index * band + band / 2}
              y={height - 8}
              textAnchor="middle"
              fontSize={11}
              fill="var(--text-muted)"
            >
              {datum.label}
            </text>
          ) : null,
        )}

        {/* Hit targets span the whole band, so a 1px column is still hoverable. */}
        {data.map((datum, index) => (
          <rect
            key={`hit-${datum.key}`}
            x={PAD.left + index * band}
            y={PAD.top}
            width={band}
            height={plotHeight}
            fill="transparent"
            onMouseEnter={() => setHovered(index)}
            onMouseLeave={() => setHovered(null)}
          >
            <title>{datum.tooltip}</title>
          </rect>
        ))}
      </svg>

      {hovered !== null && (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: `${((PAD.left + hovered * band + band / 2) / width) * 100}%`,
            transform: "translateX(-50%)",
            background: "var(--surface-1)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            padding: "6px 10px",
            fontSize: 12,
            color: "var(--text-primary)",
            pointerEvents: "none",
            whiteSpace: "nowrap",
            boxShadow: "0 2px 10px rgba(0,0,0,0.10)",
            zIndex: 2,
          }}
        >
          {data[hovered].tooltip}
        </div>
      )}
    </div>
  );
}
