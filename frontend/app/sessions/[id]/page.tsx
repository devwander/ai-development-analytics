import { notFound } from "next/navigation";

import { Card, Hint, Notice, SourcePill, StatTile, StatusPill, Th } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import {
  CHANGE_TYPE_LABEL,
  TASK_TYPE_LABEL,
  formatCost,
  formatDateTime,
  formatDuration,
  formatNumber,
  formatTokens,
} from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function SessionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let session, usage, tools, git, analysis, prompts, survey;
  try {
    [session, usage, tools, git, analysis, prompts, survey] = await Promise.all([
      api.session(id),
      api.usage(id),
      api.tools(id),
      api.git(id),
      api.analysis(id),
      api.messages(id, { message_type: "human_prompt", limit: 5 }),
      api.survey(id),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    return (
      <>
        <a className="back-link" href="/sessions">
          ← Sessões
        </a>
        <h1>Sessão</h1>
        <Notice variant="error">
          <strong>API indisponível.</strong>{" "}
          {error instanceof ApiError ? error.message : "Erro inesperado."}
        </Notice>
      </>
    );
  }

  const projectFiles = session.files.filter((file) => file.is_within_project !== false);
  const scratchFiles = session.files.filter((file) => file.is_within_project === false);

  return (
    <>
      <a className="back-link" href="/sessions">
        ← Sessões
      </a>

      <h1>{session.title ?? "Sessão sem título"}</h1>
      <p className="page-note">
        <span className="mono">{session.claude_session_id}</span>
        {" · "}
        {session.project_name ?? "projeto indeterminado"}
        {" · "}
        <StatusPill status={session.status} />
      </p>

      <div className="kpi-row">
        <StatTile
          label="Trabalho"
          value={formatDuration(session.active_turn_seconds)}
          note="turnos ativos"
          term="activeTurn"
          hero
        />
        <StatTile
          label="Duração"
          value={formatDuration(session.duration_seconds)}
          note="tempo de parede"
          term="wallClock"
        />
        <StatTile
          label="Tokens"
          value={formatTokens(session.total_tokens)}
          note={session.usage_source ?? undefined}
          term="tokens"
        />
        <StatTile
          label="Custo equivalente"
          value={formatCost(session.cost_usd)}
          term="cost"
        />
        <StatTile
          label="Prompts"
          value={formatNumber(session.human_prompt_count)}
          note={`${session.confirmed_human_prompt_count} confirmados`}
          term="humanPrompts"
        />
      </div>

      <div className="section grid-2">
        <Card title="Interação observada">
          <dl className="def-grid">
            <dt>Período</dt>
            <dd>
              {formatDateTime(session.started_at)} → {formatDateTime(session.ended_at)}
            </dd>
            <dt>Modelos</dt>
            <dd>{session.models_used.join(", ") || "n/d"}</dd>
            <dt>
              Mensagens do modelo
              <Hint term="assistantMessages" />
            </dt>
            <dd>
              {formatNumber(session.assistant_message_count)}{" "}
              <span className="muted">
                ({formatNumber(session.assistant_record_count)} registros no transcript)
              </span>
            </dd>
            <dt>
              Chamadas de ferramenta
              <Hint term="toolCalls" />
            </dt>
            <dd>
              {formatNumber(session.tool_call_count)}{" "}
              <span className="muted">({session.tool_error_count} com erro)</span>
            </dd>
            <dt>
              Blocos de raciocínio
              <Hint term="thinkingBlocks" />
            </dt>
            <dd>{formatNumber(session.thinking_block_count)}</dd>
            <dt>
              Mensagens injetadas
              <Hint term="injectedMessages" />
            </dt>
            <dd>
              {formatNumber(session.injected_user_message_count)}{" "}
              <span className="muted">(resumos de compactação e notas do sistema)</span>
            </dd>
            <dt>
              Caminho do projeto
              <Hint term="projectPath" />
            </dt>
            <dd className="mono">
              {session.project_path ?? "n/d"}{" "}
              <span className="muted">
                {session.project_path_source ? `(via ${session.project_path_source})` : ""}
              </span>
            </dd>
            {session.git_branches.length > 0 && (
              <>
                <dt>Branches</dt>
                <dd>{session.git_branches.join(", ")}</dd>
              </>
            )}
          </dl>
        </Card>

        <Card title="Ferramentas" subtitle={`${tools.calls.total} chamadas no total`}>
          {tools.stats.length === 0 ? (
            <p className="empty">Nenhuma ferramenta usada.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Ferramenta</th>
                    <th className="num">Chamadas</th>
                    <th className="num">Erros</th>
                  </tr>
                </thead>
                <tbody>
                  {tools.stats.map((stat) => (
                    <tr key={stat.tool_name}>
                      <td className="mono">{stat.tool_name}</td>
                      <td className="num">{stat.calls}</td>
                      <td className="num">
                        {stat.errors > 0 ? stat.errors : <span className="muted">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <div className="section">
        <Card
          title="Tokens e custo por fonte"
          term="usageSource"
          subtitle="As fontes descrevem os mesmos tokens e discordam por construção — nunca some as linhas."
        >
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <Th term="usageSource">Fonte</Th>
                  <th className="num">Entrada</th>
                  <th className="num">Saída</th>
                  <Th numeric term="cacheWrite">Cache escrito</Th>
                  <Th numeric term="cacheRead">Cache lido</Th>
                  <Th numeric term="tokens">Total</Th>
                  <Th numeric term="cost">Custo</Th>
                </tr>
              </thead>
              <tbody>
                {usage.totals.map((row) => (
                  <tr key={row.source}>
                    <td>
                      <SourcePill source={row.source} />
                      {usage.preferred?.source === row.source && (
                        <span className="cell-sub">usada nos totais</span>
                      )}
                    </td>
                    <td className="num">{formatNumber(row.input_tokens)}</td>
                    <td className="num">{formatNumber(row.output_tokens)}</td>
                    <td className="num">{formatNumber(row.cache_creation_tokens)}</td>
                    <td className="num">{formatNumber(row.cache_read_tokens)}</td>
                    <td className="num">{formatTokens(row.total_tokens)}</td>
                    <td className="num">{formatCost(row.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {usage.totals.flatMap((row) => row.notes).length > 0 && (
            <>
              {/* Verbatim collector output, stored with the row. Not translated:
                  the UI must not restate what the record says differently. */}
              <h2 style={{ marginTop: 16 }}>Notas do coletor</h2>
              <ul className="cell-sub" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {usage.totals.flatMap((row) =>
                row.notes.map((note) => <li key={`${row.source}-${note}`}>{note}</li>),
              )}
              </ul>
            </>
          )}
        </Card>
      </div>

      <div className="section">
        <Card
          title={`Prompts do desenvolvedor (${prompts.total})`}
          term="humanPrompts"
          subtitle={
            <a
              href={`/sessions/${session.claude_session_id}/conversation`}
              style={{ textDecoration: "underline" }}
            >
              Ver a conversa completa, sem cortes →
            </a>
          }
        >
          {prompts.items.length === 0 ? (
            <p className="empty">Nenhum prompt humano registrado.</p>
          ) : (
            <ol style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 14 }}>
              {prompts.items.map((message) => (
                <li key={message.sequence}>
                  <div className="cell-sub">
                    {formatDateTime(message.timestamp)}
                    {message.origin_kind === "human" ? "" : " · origem não confirmada"}
                  </div>
                  {/* A preview here; the conversation page shows it whole. */}
                  <div className="prose">
                    {(message.content ?? "").slice(0, 600)}
                    {(message.content ?? "").length > 600 ? " […]" : ""}
                  </div>
                </li>
              ))}
            </ol>
          )}
          {prompts.total > prompts.items.length && (
            <p className="cell-sub" style={{ marginBottom: 0 }}>
              Mostrando os {prompts.items.length} primeiros de {prompts.total}.{" "}
              <a
                href={`/sessions/${session.claude_session_id}/conversation?view=prompts`}
                style={{ textDecoration: "underline" }}
              >
                Ver todos
              </a>
              .
            </p>
          )}
        </Card>
      </div>

      <div className="section grid-2">
        <Card
          title={`Arquivos do projeto (${projectFiles.length})`}
          term="filesOutsideProject"
          subtitle={
            scratchFiles.length > 0
              ? `${scratchFiles.length} arquivo(s) de trabalho fora do projeto não contam como alteração de código.`
              : undefined
          }
        >
          {projectFiles.length === 0 ? (
            <p className="empty">
              Nenhum arquivo rastreado. A sessão pode ter editado via shell — o transcript
              não registra isso.
            </p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <Th term="filesTouched">Arquivo</Th>
                    <th>Mudança</th>
                    <Th numeric term="churn">+/−</Th>
                  </tr>
                </thead>
                <tbody>
                  {projectFiles.map((file) => (
                    <tr key={file.file_path}>
                      <td className="mono">
                        {session.project_path && file.file_path.startsWith(`${session.project_path}/`)
                          ? file.file_path.slice(session.project_path.length + 1)
                          : file.file_path}
                      </td>
                      <td>{CHANGE_TYPE_LABEL[file.change_type] ?? file.change_type}</td>
                      <td className="num">
                        {file.insertions || file.deletions ? (
                          `+${file.insertions ?? 0}/−${file.deletions ?? 0}`
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card
          title="Git"
          term="linkedCommits"
          subtitle="Associação temporal e por sobreposição de arquivos. Não é prova de autoria."
        >
          {git.associations.length === 0 ? (
            <p className="empty">Nenhum commit associado.</p>
          ) : (
            <>
              <p className="cell-sub" style={{ marginTop: 0 }}>
                {git.likely_related_count} de {git.associations.length} commits com
                evidência de ligação.
              </p>
              <div style={{ display: "grid", gap: 14 }}>
                {git.associations.map((association) => (
                  <div key={association.commit.commit_hash}>
                    <div className="cell-strong">{association.commit.message}</div>
                    <div className="cell-sub mono">
                      {association.commit.commit_hash.slice(0, 10)} ·{" "}
                      {association.commit.author_name} · +{association.commit.insertions}/−
                      {association.commit.deletions} em {association.commit.files_changed}{" "}
                      arquivo(s)
                    </div>
                    <div style={{ marginTop: 4 }}>
                      <span className="pill">
                        <span
                          className="pill-dot"
                          style={{
                            background: association.likely_related
                              ? "var(--status-good)"
                              : "var(--text-muted)",
                          }}
                        />
                        {association.likely_related
                          ? "provavelmente relacionado"
                          : "sem evidência de ligação"}
                      </span>
                    </div>
                    <ul className="cell-sub" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                      {association.reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </>
          )}
        </Card>
      </div>

      {/* Declared by the developer - neither observed nor inferred. */}
      <div className="section">
        <Card
          title="Percepção declarada"
          term="perceivedQuality"
          subtitle="Respondida pelo desenvolvedor. Não sai de nenhum transcript."
        >
          {survey === null ? (
            <p className="empty">
              Sem resposta. Registre com{" "}
              <code className="mono">
                app.cli.main survey {session.claude_session_id} --quality 4
                --productivity 5 --effort 3 --trust 4
              </code>
            </p>
          ) : (
            <>
              <dl className="def-grid">
                <dt>
                  Qualidade percebida
                  <Hint term="perceivedQuality" />
                </dt>
                <dd>{survey.perceived_quality ?? "não respondido"} <span className="muted">(1 ruim · 5 ótima)</span></dd>
                <dt>
                  Produtividade percebida
                  <Hint term="perceivedProductivity" />
                </dt>
                <dd>{survey.perceived_productivity ?? "não respondido"} <span className="muted">(1 baixa · 5 alta)</span></dd>
                <dt>
                  Esforço mental
                  <Hint term="perceivedEffort" />
                </dt>
                <dd>{survey.perceived_effort ?? "não respondido"} <span className="muted">(1 leve · 5 pesado)</span></dd>
                <dt>
                  Confiança na saída
                  <Hint term="trust" />
                </dt>
                <dd>{survey.trust_in_output ?? "não respondido"} <span className="muted">(1 nenhuma · 5 total)</span></dd>
                <dt>
                  Retrabalho declarado
                  <Hint term="reworkReported" />
                </dt>
                <dd>
                  {survey.rework_reported === null
                    ? "não respondido"
                    : survey.rework_reported
                      ? "sim"
                      : "não"}
                </dd>
                <dt>Respondido em</dt>
                <dd>
                  {formatDateTime(survey.answered_at)}
                  {survey.respondent ? ` · ${survey.respondent}` : ""}
                </dd>
              </dl>
              {survey.comment && (
                <p className="prose" style={{ marginTop: 12 }}>
                  {survey.comment}
                </p>
              )}
              {analysis !== null &&
                analysis.requires_rework !== null &&
                survey.rework_reported !== null &&
                analysis.requires_rework !== survey.rework_reported && (
                  <Notice>
                    <strong>Divergência.</strong> A análise por IA
                    {analysis.requires_rework ? " identificou" : " não identificou"}{" "}
                    retrabalho, mas o desenvolvedor declarou o contrário. Vale
                    olhar — é exatamente o tipo de caso que interessa comparar.
                  </Notice>
                )}
            </>
          )}
        </Card>
      </div>

      {/* The only inferred section on the page. */}
      <div className="section">
        <Card
          title="Análise por IA — dado inferido"
          term="aiAnalysis"
          subtitle="Produzido por um modelo de linguagem a partir de um resumo da sessão. Não é observação."
        >
          {analysis === null ? (
            <p className="empty">
              Sessão ainda não analisada. Habilite{" "}
              <code className="mono">AI_ANALYSIS_ENABLED</code> e rode{" "}
              <code className="mono">python -m app.cli.main analyze {session.claude_session_id}</code>
              .
            </p>
          ) : (
            <>
              <dl className="def-grid">
                <dt>Objetivo</dt>
                <dd>{analysis.objective ?? "n/d"}</dd>
                <dt>Resumo</dt>
                <dd>{analysis.summary ?? "n/d"}</dd>
                <dt>Resultado</dt>
                <dd>{analysis.result ?? <span className="muted">não determinado</span>}</dd>
                <dt>Categoria</dt>
                <dd>
                  {analysis.task_type
                    ? (TASK_TYPE_LABEL[analysis.task_type] ?? analysis.task_type)
                    : "n/d"}
                </dd>
                <dt>Complexidade</dt>
                <dd>{analysis.complexity ?? "n/d"}</dd>
                <dt>Sucesso</dt>
                <dd>
                  {analysis.success === null ? (
                    <span className="muted">não determinado</span>
                  ) : analysis.success ? (
                    "sim"
                  ) : (
                    "não"
                  )}
                </dd>
                <dt>Retrabalho</dt>
                <dd>{analysis.requires_rework ? "identificado" : "não identificado"}</dd>
                <dt>
                  Confiança
                  <Hint term="confidence" />
                </dt>
                <dd>{analysis.confidence !== null ? analysis.confidence.toFixed(2) : "n/d"}</dd>
                <dt>Produzido por</dt>
                <dd className="mono">
                  {analysis.provider} · {analysis.model} ·{" "}
                  {formatDateTime(analysis.created_at)}
                </dd>
              </dl>

              {analysis.rework_evidence.length > 0 && (
                <>
                  <h2 style={{ marginTop: 18 }}>Evidências de retrabalho</h2>
                  <ul className="cell-sub" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                    {analysis.rework_evidence.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </>
              )}

              {analysis.technologies.length > 0 && (
                <div className="chip-row">
                  {analysis.technologies.map((tech) => (
                    <span className="pill" key={tech}>
                      {tech}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}
        </Card>
      </div>

      <div className="section">
        <Card
          title="Proveniência"
          term="provenance"
          subtitle="Permite reconstruir o caminho métrica → sessão → evento → transcript original."
        >
          {session.source ? (
            <dl className="def-grid">
              <dt>Transcript</dt>
              <dd className="mono">{session.source.transcript_path}</dd>
              <dt>SHA-256</dt>
              <dd className="mono">{session.source.transcript_sha256}</dd>
              <dt>Linhas</dt>
              <dd>
                {formatNumber(session.source.parsed_lines)} lidas ·{" "}
                {session.source.unparseable_lines} corrompidas ·{" "}
                {formatNumber(session.source.total_lines)} total
              </dd>
              <dt>Collector</dt>
              <dd className="mono">{session.source.collector_version}</dd>
            </dl>
          ) : (
            <p className="empty">Sem registro de proveniência.</p>
          )}

          {session.collection_warnings.length > 0 && (
            <>
              <h2 style={{ marginTop: 18 }}>Avisos da coleta</h2>
              <ul className="cell-sub" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {session.collection_warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>
    </>
  );
}
