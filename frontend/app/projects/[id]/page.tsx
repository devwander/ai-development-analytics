import { notFound } from "next/navigation";

import {
  DailyCharts,
  KpiRows,
  MissingUsageNotice,
  StatusAndCategories,
} from "@/components/OverviewPanels";
import {
  AttributionCard,
  BaselineCard,
  SurveyCard,
  ValidityCard,
} from "@/components/ResearchPanels";
import { PeriodFilter, readPeriod } from "@/components/PeriodFilter";
import { SessionDownloadForm } from "@/components/SessionDownloadForm";
import { Card, Notice, SourcePill, StatusPill, Th } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  formatCost,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatTokens,
  shortSessionId,
} from "@/lib/format";

export const dynamic = "force-dynamic";

const RECENT_SESSIONS = 10;

export default async function ProjectPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const period = readPeriod(await searchParams);
  const projectId = Number(id);
  if (!Number.isInteger(projectId) || projectId <= 0) notFound();

  let analytics, sessions;
  try {
    [analytics, sessions] = await Promise.all([
      api.projectAnalytics(projectId, period),
      api.sessions({
        project_id: projectId,
        limit: RECENT_SESSIONS,
        since: period.since,
        until: period.until,
      }),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return (
      <>
        <a className="back-link" href="/projects">
          ← Projetos
        </a>
        <h1>Projeto</h1>
        <Notice variant="error">
          <strong>API indisponível.</strong>{" "}
          {error instanceof ApiError ? error.message : "Erro inesperado."}
        </Notice>
      </>
    );
  }

  const { project } = analytics;
  const errorRate =
    analytics.tool_calls > 0 ? (analytics.tool_errors / analytics.tool_calls) * 100 : null;

  return (
    <>
      <a className="back-link" href="/projects">
        ← Projetos
      </a>

      <h1>{project.name}</h1>
      <p className="page-note">
        <span className="mono">{project.path}</span>
        {project.repository_url ? (
          <>
            {" · "}
            <span className="mono">{project.repository_url}</span>
          </>
        ) : (
          " · sem repositório Git"
        )}
        <br />
        Os mesmos indicadores da visão geral, recortados para este projeto. Tudo
        observado, exceto a seção marcada como inferida.
      </p>

      <PeriodFilter
        basePath={`/projects/${projectId}`}
        since={period.since}
        until={period.until}
        invalid={period.invalid}
        matchedSessions={analytics.sessions}
      />

      {/* "Projetos: 1" seria ruído aqui. */}
      <KpiRows overview={analytics} showProjects={false} />

      {/* Before the charts: what the sample can and cannot support. */}
      {analytics.attribution && analytics.coverage && (
        <div className="section grid-2">
          <AttributionCard
            attribution={analytics.attribution}
            coverage={analytics.coverage}
          />
          {analytics.validity && analytics.effort_shape && (
            <ValidityCard
              validity={analytics.validity}
              effort={analytics.effort_shape}
            />
          )}
        </div>
      )}

      {analytics.survey && (
        <div className="section grid-2">
          <SurveyCard survey={analytics.survey} sessions={analytics.sessions} />
          <BaselineCard baseline={analytics.baseline ?? null} />
        </div>
      )}

      <DailyCharts overview={analytics} />

      <StatusAndCategories
        overview={analytics}
        categoriesEmptyMessage="Nenhuma sessão deste projeto foi analisada. Habilite AI_ANALYSIS_ENABLED e rode a análise."
      />

      <div className="section">
        <Card
          title="Ferramentas mais usadas"
          subtitle={
            errorRate !== null
              ? `${formatNumber(analytics.tool_calls)} chamadas no projeto · ${errorRate.toFixed(1)}% com erro`
              : "Nenhuma chamada registrada."
          }
        >
          {analytics.tools.length === 0 ? (
            <p className="empty">Nenhuma ferramenta usada neste projeto.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Ferramenta</th>
                    <Th numeric term="toolCalls">Chamadas</Th>
                    <Th numeric term="toolErrors">Erros</Th>
                    <th className="num">Participação</th>
                  </tr>
                </thead>
                <tbody>
                  {analytics.tools.slice(0, 15).map((tool) => (
                    <tr key={tool.tool_name}>
                      <td className="mono">{tool.tool_name}</td>
                      <td className="num">{formatNumber(tool.calls)}</td>
                      <td className="num">
                        {tool.errors > 0 ? tool.errors : <span className="muted">—</span>}
                      </td>
                      <td className="num">
                        {analytics.tool_calls > 0
                          ? `${((tool.calls / analytics.tool_calls) * 100).toFixed(1)}%`
                          : "n/d"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {analytics.tools.length > 15 && (
                <p className="cell-sub" style={{ marginBottom: 0 }}>
                  + {analytics.tools.length - 15} ferramenta(s) com menos chamadas
                </p>
              )}
            </div>
          )}
        </Card>
      </div>

      <div className="section">
        <Card
          title={`Sessões recentes (${sessions.total} no total)`}
          subtitle={
            <a href={`/sessions?project_id=${project.id}`} style={{ textDecoration: "underline" }}>
              Ver todas as sessões deste projeto
            </a>
          }
        >
          {sessions.items.length === 0 ? (
            <p className="empty">Nenhuma sessão neste projeto.</p>
          ) : (
            <SessionDownloadForm>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th className="select-col" aria-hidden />
                      <th>Sessão</th>
                      <th>Início</th>
                      <Th numeric term="activeTurn">Trabalho</Th>
                      <Th numeric term="humanPrompts">Prompts</Th>
                      <Th numeric term="tokens">Tokens</Th>
                      <Th numeric term="cost">Custo eq.</Th>
                      <Th term="usageSource">Fonte</Th>
                      <Th term="sessionStatus">Status</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.items.map((session) => (
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
                          </div>
                        </td>
                        <td className="cell-sub">{formatDateTime(session.started_at)}</td>
                        <td className="num">{formatDuration(session.active_turn_seconds)}</td>
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
        </Card>
      </div>

      <MissingUsageNotice overview={analytics} />
    </>
  );
}
