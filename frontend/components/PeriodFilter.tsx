import { formatDate } from "@/lib/format";

/**
 * The date-range control.
 *
 * Composition rules it follows: one row, left-aligned, above everything it
 * scopes - never inside a card and never per-chart. Presets come before the
 * custom range, because nobody fights a calendar to ask for "last 30 days".
 *
 * Built from links and a plain GET form, so it works in Server Components with
 * no JavaScript: the range lives in the URL, which also makes any filtered view
 * a shareable, citable address.
 */

export interface PeriodParams {
  since?: string;
  until?: string;
  /** The URL carried a backwards range, which was dropped rather than sent. */
  invalid?: boolean;
}

interface Preset {
  label: string;
  days: number | null;
}

const PRESETS: Preset[] = [
  { label: "todo o período", days: null },
  { label: "7 dias", days: 7 },
  { label: "30 dias", days: 30 },
  { label: "90 dias", days: 90 },
];

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function presetSince(days: number): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - (days - 1));
  return isoDay(date);
}

function buildHref(
  basePath: string,
  carry: Record<string, string | undefined>,
  period: PeriodParams,
): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(carry)) {
    if (value) query.set(key, value);
  }
  if (period.since) query.set("since", period.since);
  if (period.until) query.set("until", period.until);
  const suffix = query.toString();
  return suffix ? `${basePath}?${suffix}` : basePath;
}

export function PeriodFilter({
  basePath,
  since,
  until,
  carry = {},
  matchedSessions,
  invalid = false,
}: {
  basePath: string;
  since?: string;
  until?: string;
  invalid?: boolean;
  /** Other query params to preserve when the range changes. */
  carry?: Record<string, string | undefined>;
  /** How many sessions the current range captured, for the summary line. */
  matchedSessions?: number;
}) {
  const active = Boolean(since || until);
  const today = isoDay(new Date());

  return (
    <div className="period">
      <div className="period-row">
        <span className="period-label">Período</span>
        {PRESETS.map((preset) => {
          const target =
            preset.days === null
              ? {}
              : { since: presetSince(preset.days), until: today };
          const selected =
            preset.days === null ? !active : since === target.since && until === target.until;
          return (
            <a
              key={preset.label}
              href={buildHref(basePath, carry, target)}
              aria-current={selected ? "true" : undefined}
            >
              {selected ? "✓ " : ""}
              {preset.label}
            </a>
          );
        })}

        <form method="get" action={basePath} className="period-custom">
          {Object.entries(carry).map(([key, value]) =>
            value ? <input key={key} type="hidden" name={key} value={value} /> : null,
          )}
          <label>
            de
            <input type="date" name="since" defaultValue={since ?? ""} max={today} />
          </label>
          <label>
            até
            <input type="date" name="until" defaultValue={until ?? ""} max={today} />
          </label>
          <button type="submit">aplicar</button>
        </form>
      </div>

      {invalid && (
        <p className="period-note">
          <strong>Intervalo invertido</strong> — a data inicial vinha depois da
          final, então o filtro foi ignorado e os números abaixo cobrem todo o
          período.
        </p>
      )}

      {active && (
        <p className="period-note">
          Mostrando{" "}
          {matchedSessions !== undefined && <strong>{matchedSessions} sessão(ões) </strong>}
          {since ? `de ${formatDate(since)}` : "desde o início"}{" "}
          {until ? `até ${formatDate(until)}` : "até hoje"}. O filtro é aplicado à
          data de início da sessão e escopa todos os números abaixo — inclusive
          cobertura e atribuição, que passam a descrever apenas esta janela.
          Sessões sem data de início registrada ficam de fora.
        </p>
      )}
    </div>
  );
}

/**
 * Reads the range out of a page's search params.
 *
 * A backwards range is dropped here rather than sent on: the API rejects it with
 * a 422, and a page that surfaced that would tell the reader the API is down
 * when in fact their dates are the wrong way round.
 */
export function readPeriod(
  params: Record<string, string | string[] | undefined>,
): PeriodParams {
  const since = typeof params.since === "string" && params.since ? params.since : undefined;
  const until = typeof params.until === "string" && params.until ? params.until : undefined;
  if (since && until && since > until) {
    return { invalid: true };
  }
  return { since, until };
}
