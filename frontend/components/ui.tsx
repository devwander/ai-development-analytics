import type { ReactNode } from "react";

import { STATUS_LABEL, UNKNOWN } from "@/lib/format";
import { HINTS, type HintKey } from "@/lib/metrics";

/**
 * A field description, shown on hover and focus.
 *
 * Uses the native ``title`` tooltip on purpose: it needs no JavaScript, so it
 * works inside Server Components, and it survives print and reader modes. The
 * span is focusable so the definition is reachable by keyboard rather than by
 * pointer alone.
 */
export function Hint({ term }: { term: HintKey }) {
  const text = HINTS[term];
  return (
    <span className="hint" tabIndex={0} title={text} aria-label={text} role="note">
      i
    </span>
  );
}

/** A table header carrying its own definition. */
export function Th({
  children,
  term,
  numeric = false,
}: {
  children: ReactNode;
  term?: HintKey;
  numeric?: boolean;
}) {
  return (
    <th className={numeric ? "num" : undefined}>
      {children}
      {term ? <Hint term={term} /> : null}
    </th>
  );
}

export function StatTile({
  label,
  value,
  note,
  hero = false,
  term,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  hero?: boolean;
  term?: HintKey;
}) {
  return (
    <div className={hero ? "stat hero" : "stat"}>
      <div className="stat-label">
        {label}
        {term ? <Hint term={term} /> : null}
      </div>
      {/* Proportional figures: tabular-nums makes a large standalone value
          look loose, and is reserved for columns that must align. */}
      <div className="stat-value">{value}</div>
      {note ? <div className="stat-note">{note}</div> : null}
    </div>
  );
}

export function Card({
  title,
  subtitle,
  children,
  term,
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  term?: HintKey;
}) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>
          {title}
          {term ? <Hint term={term} /> : null}
        </h2>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {children}
    </section>
  );
}

/** Status colours are reserved and always ship with a label, never colour alone. */
const STATUS_COLOR: Record<string, string> = {
  active: "var(--status-warning)",
  completed: "var(--status-good)",
  interrupted: "var(--status-serious)",
  unknown: "var(--text-muted)",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <span className="pill">
      <span
        className="pill-dot"
        style={{ background: STATUS_COLOR[status] ?? "var(--text-muted)" }}
      />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

/**
 * Every token or cost figure names the source that produced it. ccusage and
 * the transcript describe the same tokens and disagree by construction, so a
 * number without its source is not interpretable.
 */
export function SourcePill({ source }: { source: string | null }) {
  if (!source) return <span className="muted">{UNKNOWN}</span>;
  return <span className="pill">{source === "ccusage" ? "ccusage" : "transcript"}</span>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Notice({
  children,
  variant = "warning",
}: {
  children: ReactNode;
  variant?: "warning" | "error";
}) {
  return (
    <div className={variant === "error" ? "notice notice-error" : "notice"}>
      {children}
    </div>
  );
}
