import { PeriodFilter, readPeriod } from "@/components/PeriodFilter";
import { SessionDownloadForm } from "@/components/SessionDownloadForm";
import { Card, Notice, SourcePill, StatusPill, Th } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatTokens,
  shortSessionId,
} from "@/lib/format";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 25;

const STATUS_FILTERS = [
  { value: "", label: "todas" },
  { value: "completed", label: "concluídas" },
  { value: "interrupted", label: "interrompidas" },
  { value: "active", label: "ativas" },
  { value: "unknown", label: "indeterminadas" },
];

/**
 * Always returns an absolute path.
 *
 * An empty `href=""` resolves to the *current* URL including its query string,
 * so a "clear the filter" link built that way silently keeps the filter it was
 * meant to remove.
 */
function buildQuery(params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const suffix = query.toString();
  return suffix ? `/sessions?${suffix}` : "/sessions";
}

export default async function SessionsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const status = typeof params.status === "string" ? params.status : "";
  const projectId = typeof params.project_id === "string" ? params.project_id : "";
  const offset = Number(params.offset ?? 0) || 0;
  const period = readPeriod(params);

  let page;
  try {
    page = await api.sessions({
      limit: PAGE_SIZE,
      offset,
      status: status || undefined,
      project_id: projectId || undefined,
      since: period.since,
      until: period.until,
    });
  } catch (error) {
    return (
      <>
        <h1>Sessões</h1>
        <Notice variant="error">
          <strong>API indisponível.</strong>{" "}
          {error instanceof ApiError ? error.message : "Erro inesperado."}
        </Notice>
      </>
    );
  }

  const shown = page.items.length;
  const hasPrevious = offset > 0;
  const hasNext = offset + shown < page.total;

  return (
    <>
      <h1>Sessões</h1>
      <p className="page-note">
        Duração é o tempo de parede da sessão; <strong>trabalho</strong> é a soma dos
        turnos ativos. As duas divergem muito porque sessões atravessam dias — só a
        segunda serve como denominador de produtividade.
      </p>

      <PeriodFilter
        basePath="/sessions"
        since={period.since}
        until={period.until}
        invalid={period.invalid}
        carry={{ status: status || undefined, project_id: projectId || undefined }}
        matchedSessions={page.total}
      />

      <div className="filters">
        {STATUS_FILTERS.map((filter) => (
          <a
            key={filter.value || "all"}
            href={buildQuery({
              status: filter.value,
              project_id: projectId,
              since: period.since,
              until: period.until,
            })}
            aria-current={status === filter.value ? "true" : undefined}
          >
            {filter.label}
          </a>
        ))}
      </div>

      <Card
        title={`${page.total} sessão(ões)`}
        subtitle={
          projectId
            ? "Filtrado por projeto. Remova o filtro voltando em Projetos."
            : "Ordenadas da mais recente para a mais antiga."
        }
      >
        {shown === 0 ? (
          <p className="empty">Nenhuma sessão para este filtro.</p>
        ) : (
          <SessionDownloadForm>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="select-col" aria-hidden />
                    <th>Sessão</th>
                    <th>Projeto</th>
                    <th>Início</th>
                    <Th numeric term="activeTurn">Trabalho</Th>
                    <Th numeric term="wallClock">Duração</Th>
                    <Th numeric term="humanPrompts">Prompts</Th>
                    <Th numeric term="tokens">Tokens</Th>
                    <Th numeric term="cost">Custo eq.</Th>
                    <Th term="usageSource">Fonte</Th>
                    <Th term="sessionStatus">Status</Th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((session) => (
                    <tr key={session.claude_session_id}>
                      <td className="select-col">
                        <input
                          type="checkbox"
                          name="id"
                          value={session.id}
                          aria-label={`Selecionar sessão ${session.title ?? session.claude_session_id}`}
                        />
                      </td>
                      <td>
                        <a
                          href={`/sessions/${session.claude_session_id}`}
                          className="cell-strong"
                        >
                          {session.title ?? shortSessionId(session.claude_session_id)}
                        </a>
                        <div className="cell-sub mono">
                          {shortSessionId(session.claude_session_id)}
                          {session.models_used.length > 0
                            ? ` · ${session.models_used.join(", ")}`
                            : ""}
                        </div>
                      </td>
                      <td>{session.project_name ?? <span className="muted">n/d</span>}</td>
                      <td className="cell-sub">{formatDateTime(session.started_at)}</td>
                      <td className="num">{formatDuration(session.active_turn_seconds)}</td>
                      <td className="num muted">{formatDuration(session.duration_seconds)}</td>
                      <td className="num">{session.human_prompt_count}</td>
                      <td className="num">{formatTokens(session.total_tokens)}</td>
                      <td className="num">{formatCost(session.cost_usd)}</td>
                      <td>
                        <SourcePill source={session.usage_source} />
                      </td>
                      <td>
                        <StatusPill status={session.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </SessionDownloadForm>
        )}

        <div className="pager">
          {hasPrevious && (
            <a
              href={buildQuery({
                status,
                project_id: projectId,
                since: period.since,
                until: period.until,
                offset: Math.max(0, offset - PAGE_SIZE),
              })}
            >
              ← anteriores
            </a>
          )}
          <span className="muted">
            {shown === 0 ? 0 : offset + 1}–{offset + shown} de {page.total}
          </span>
          {hasNext && (
            <a
              href={buildQuery({
                status,
                project_id: projectId,
                since: period.since,
                until: period.until,
                offset: offset + PAGE_SIZE,
              })}
            >
              próximas →
            </a>
          )}
        </div>
      </Card>
    </>
  );
}
