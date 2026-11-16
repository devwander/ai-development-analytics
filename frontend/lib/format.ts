/**
 * Formatting helpers.
 *
 * The recurring rule: an absent value is shown as "n/d", never as zero. The
 * backend deliberately returns null when no source could determine a cost, and
 * rendering that as $0.00 would turn "unknown" into a claim.
 */

export const UNKNOWN = "n/d";

function ptBR(value: number, decimals: number): string {
  return value.toLocaleString("pt-BR", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

export function formatTokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  if (value >= 1_000_000_000) return `${ptBR(value / 1_000_000_000, 2)}B`;
  if (value >= 1_000_000) return `${ptBR(value / 1_000_000, 1)}M`;
  if (value >= 1_000) return `${ptBR(value / 1_000, 1)}K`;
  return ptBR(value, 0);
}

export function formatCost(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  // A real sub-cent cost rounded to "0,00" reads as free. Say it is small
  // instead - the same reason an unknown cost is never rendered as zero.
  if (value > 0 && value < 0.005) return "< US$ 0,01";
  if (value >= 1000) return `US$ ${ptBR(value, 0)}`;
  return `US$ ${ptBR(value, 2)}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return UNKNOWN;
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (hours >= 1) return `${hours}h${String(minutes).padStart(2, "0")}m`;
  if (minutes >= 1) return `${minutes}m`;
  return `${total}s`;
}

export function formatHours(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return UNKNOWN;
  return `${(seconds / 3600).toFixed(1)}h`;
}

/** Matches a date-only value like 2026-08-01, with no time or zone. */
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * Timezone-dependent for timestamps: the server formats in the server's zone, a
 * browser in the viewer's. Safe today because only Server Components call this -
 * their output is not re-rendered on the client. Moving it into a Client
 * Component would produce a real hydration mismatch; pass a pre-formatted string
 * down instead.
 *
 * A **date-only** value is formatted from its own digits and never goes through
 * `Date`. `new Date("2026-08-01")` is midnight UTC, which in any negative offset
 * renders as the day before - a filter set to 01/08 would summarise itself as
 * 31/07, and the control would contradict the text beside it.
 */
export function formatDate(value: string | null | undefined): string {
  if (!value) return UNKNOWN;
  const dateOnly = DATE_ONLY.exec(value);
  if (dateOnly) {
    const [, year, month, day] = dateOnly;
    return `${day}/${month}/${year}`;
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return UNKNOWN;
  return date.toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

/** Timezone-dependent - see the note on formatDate. Server Components only. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return UNKNOWN;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return UNKNOWN;
  return date.toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatDayLabel(day: string): string {
  const [, month, dayOfMonth] = day.split("-");
  return `${dayOfMonth}/${month}`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNKNOWN;
  return value.toLocaleString("pt-BR");
}

export function shortSessionId(id: string): string {
  return id.slice(0, 8);
}

/** Human label for a session status; the raw value is a backend enum. */
export const STATUS_LABEL: Record<string, string> = {
  active: "ativa",
  completed: "concluída",
  interrupted: "interrompida",
  unknown: "indeterminada",
};

export const TASK_TYPE_LABEL: Record<string, string> = {
  feature: "funcionalidade",
  bug_fix: "correção",
  debugging: "depuração",
  refactoring: "refatoração",
  testing: "testes",
  documentation: "documentação",
  research: "pesquisa",
  configuration: "configuração",
  deployment: "implantação",
  code_review: "revisão de código",
  other: "outro",
};

export const CHANGE_TYPE_LABEL: Record<string, string> = {
  created: "criado",
  modified: "modificado",
  deleted: "removido",
  renamed: "renomeado",
  read: "lido",
  unknown: "indeterminado",
};
