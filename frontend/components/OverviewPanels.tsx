import { CategoryBars } from "@/components/CategoryBars";
import { ColumnChart, type ColumnDatum } from "@/components/ColumnChart";
import { Card, Notice, StatTile } from "@/components/ui";
import type { Overview } from "@/lib/api";
import {
  formatCost,
  formatDayLabel,
  formatHours,
  formatNumber,
  formatTokens,
  STATUS_LABEL,
} from "@/lib/format";

/**
 * The metric panels shared by the global overview and a single project's page.
 *
 * Scoping is the only difference between the two views, so the panels live in
 * one place: a metric that gains a caveat gains it in both, and the two can
 * never drift into telling different stories about the same numbers.
 */

export function KpiRows({
  overview,
  showProjects = true,
}: {
  overview: Overview;
  /** Off inside a single project, where "1 project" is noise. */
  showProjects?: boolean;
}) {
  const sourceEntries = Object.entries(overview.usage_sources);

  return (
    <>
      <div className="kpi-row">
        <StatTile
          label="Horas de trabalho"
          value={formatHours(overview.active_turn_seconds)}
          note="soma dos turnos ativos"
          term="activeTurn"
          hero
        />
        <StatTile label="Sessões" value={formatNumber(overview.sessions)} term="sessions" />
        {showProjects && (
          <StatTile label="Projetos" value={formatNumber(overview.projects)} />
        )}
        <StatTile label="Tokens" value={formatTokens(overview.total_tokens)} term="tokens" />
        <StatTile
          label="Custo equivalente"
          value={formatCost(overview.cost_usd)}
          term="cost"
          note={
            sourceEntries.length > 1
              ? sourceEntries.map(([name, count]) => `${count} de ${name}`).join(" · ")
              : undefined
          }
        />
      </div>

      <div className="kpi-row" style={{ marginTop: 12 }}>
        <StatTile
          label="Prompts do usuário"
          value={formatNumber(overview.human_prompts)}
          term="humanPrompts"
        />
        <StatTile
          label="Chamadas de ferramenta"
          value={formatNumber(overview.tool_calls)}
          term="toolCalls"
        />
        <StatTile
          label="Ferramentas com erro"
          value={formatNumber(overview.tool_errors)}
          term="toolErrors"
          note={
            overview.tool_calls > 0
              ? `${((overview.tool_errors / overview.tool_calls) * 100).toFixed(1)}% das chamadas`
              : undefined
          }
        />
        <StatTile
          label="Tempo de parede"
          value={formatHours(overview.wall_clock_seconds)}
          note="sessões atravessam dias — não é tempo de trabalho"
          term="wallClock"
        />
      </div>
    </>
  );
}

export function DailyCharts({ overview }: { overview: Overview }) {
  const sessionsPerDay: ColumnDatum[] = overview.per_day.map((bucket) => ({
    key: bucket.day,
    label: formatDayLabel(bucket.day),
    value: bucket.sessions,
    tooltip: `${formatDayLabel(bucket.day)} · ${bucket.sessions} sessão(ões) · ${formatHours(
      bucket.active_turn_seconds,
    )} de turnos ativos`,
  }));

  const costPerDay: ColumnDatum[] = overview.per_day.map((bucket) => ({
    key: bucket.day,
    label: formatDayLabel(bucket.day),
    value: bucket.cost_usd ?? 0,
    missing: bucket.cost_usd === null,
    tooltip: `${formatDayLabel(bucket.day)} · ${formatCost(bucket.cost_usd)} · ${formatTokens(
      bucket.total_tokens,
    )} tokens`,
  }));

  const daysWithoutCost = overview.per_day.filter((b) => b.cost_usd === null).length;

  // Two measures of different scale get two charts. Never a second y-axis.
  return (
    <div className="section grid-2">
      <Card
        title="Sessões por dia"
        subtitle="Data de início da sessão. Passe o mouse para o detalhe do dia."
      >
        <ColumnChart data={sessionsPerDay} format="number" />
      </Card>

      <Card
        title="Custo equivalente por dia"
        term="cost"
        subtitle={
          daysWithoutCost > 0
            ? `${daysWithoutCost} dia(s) sem custo determinável aparecem como contorno tracejado na linha de base — sessão sem nenhuma chamada à API, não custo zero.`
            : "Valores do ccusage quando disponíveis; do transcript caso contrário."
        }
      >
        <ColumnChart data={costPerDay} format="cost" />
      </Card>
    </div>
  );
}

export function StatusAndCategories({
  overview,
  categoriesEmptyMessage,
}: {
  overview: Overview;
  categoriesEmptyMessage: string;
}) {
  return (
    <div className="section grid-2">
      <Card
        title="Sessões por status"
        term="sessionStatus"
        subtitle="O status é heurístico: o Claude Code não grava um marcador de fim."
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Status</th>
                <th className="num">Sessões</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(overview.sessions_by_status)
                .sort((a, b) => b[1] - a[1])
                .map(([status, count]) => (
                  <tr key={status}>
                    <td>{STATUS_LABEL[status] ?? status}</td>
                    <td className="num">{count}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card
        title="Tarefas por categoria"
        term="taskType"
        subtitle="Dado inferido por IA — não é observação."
      >
        <CategoryBars
          counts={overview.task_types}
          total={overview.sessions}
          emptyMessage={categoriesEmptyMessage}
        />
      </Card>
    </div>
  );
}

export function MissingUsageNotice({ overview }: { overview: Overview }) {
  if (overview.sessions_without_usage === 0) return null;
  return (
    <div className="section">
      <Notice>
        <strong>{overview.sessions_without_usage} sessão(ões) sem dados de consumo.</strong>{" "}
        Nenhuma fonte reportou tokens para elas — os totais acima não as incluem.
      </Notice>
    </div>
  );
}
