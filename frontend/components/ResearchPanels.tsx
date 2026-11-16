import { Card, Hint } from "@/components/ui";
import type {
  Attribution,
  Baseline,
  Coverage,
  EffortShape,
  SurveyAggregate,
  ValidityThreats,
} from "@/lib/api";
import { formatDate, formatDuration, formatNumber, UNKNOWN } from "@/lib/format";
import type { HintKey } from "@/lib/metrics";

/**
 * Panels a study needs before any productivity number can be read.
 *
 * They exist to stop the dashboard reading as more certain than it is: how much
 * of the sample supports a delivery claim, how much of the committed code can
 * be traced to the AI at all, and what properties of the dataset qualify the
 * rest. None of them is a failure report - they are the caveats, on screen.
 */

function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return UNKNOWN;
  return `${(value * 100).toFixed(digits)}%`;
}

/** A proportion bar. Not a chart: one ratio against its whole. */
function Meter({ value, label }: { value: number | null; label: string }) {
  const share = value === null ? 0 : Math.max(0, Math.min(1, value));
  return (
    <div>
      <div
        style={{
          height: 10,
          borderRadius: 5,
          background: "var(--meter-track)",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${share * 100}%`,
            height: "100%",
            background: "var(--meter-fill)",
            borderRadius: 5,
          }}
        />
      </div>
      <div className="cell-sub" style={{ marginTop: 5 }}>
        {label}
      </div>
    </div>
  );
}

export function AttributionCard({
  attribution,
  coverage,
}: {
  attribution: Attribution;
  coverage: Coverage;
}) {
  const commitCoverage =
    coverage.sessions > 0 ? coverage.with_linked_commit / coverage.sessions : null;

  return (
    <Card
      title="Atribuição e cobertura"
      term="attribution"
      subtitle="Leia antes de qualquer número de produtividade."
    >
      <div className="stat-label">
        Teto de atribuição
        <Hint term="attribution" />
      </div>
      <div className="stat-value" style={{ fontSize: 34 }}>
        {percent(attribution.attribution_ratio)}
      </div>
      <p className="cell-sub" style={{ marginTop: 6 }}>
        {formatNumber(attribution.ai_insertions)} linhas vieram de uma edição
        rastreada da IA, contra {formatNumber(attribution.commit_insertions)} linhas
        nos {attribution.linked_commits} commit(s) associados.
      </p>
      <div style={{ marginTop: 12 }}>
        <Meter
          value={attribution.attribution_ratio}
          label="parcela das linhas commitadas que passou por edição rastreada"
        />
      </div>

      <p className="cell-sub" style={{ marginTop: 14 }}>
        É um <strong>teto</strong>, não uma medida de autoria: o numerador ignora
        arquivos escritos via shell, e o denominador conta o commit inteiro,
        incluindo o que foi digitado à mão. Divida linhas por hora sem isso e a
        sobre-atribuição é grande.
      </p>

      <h2 style={{ marginTop: 18 }}>O que a amostra sustenta</h2>
      <div className="table-wrap">
        <table>
          <tbody>
            <tr>
              <td>Sessões coletadas</td>
              <td className="num">{coverage.sessions}</td>
              <td className="cell-sub">base para esforço</td>
            </tr>
            <tr>
              <td>
                Com prompt humano
                <Hint term="confirmedPrompts" />
              </td>
              <td className="num">{coverage.with_prompts}</td>
              <td className="cell-sub">sessões com interação real</td>
            </tr>
            <tr>
              <td>Em projeto com Git</td>
              <td className="num">{coverage.in_git_project}</td>
              <td className="cell-sub">elegíveis para entrega</td>
            </tr>
            <tr>
              <td className="cell-strong">
                Com commit associado
                <Hint term="coverage" />
              </td>
              <td className="num cell-strong">{coverage.with_linked_commit}</td>
              <td className="cell-sub">
                base real para entrega ({percent(commitCoverage, 0)} das sessões)
              </td>
            </tr>
            <tr>
              <td>
                Com análise por IA
                <Hint term="aiAnalysis" />
              </td>
              <td className="num">{coverage.with_analysis}</td>
              <td className="cell-sub">dado inferido</td>
            </tr>
            <tr>
              <td>
                Com resposta de percepção
                <Hint term="perceivedQuality" />
              </td>
              <td className="num">{coverage.with_survey}</td>
              <td className="cell-sub">dado declarado</td>
            </tr>
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export function ValidityCard({
  validity,
  effort,
}: {
  validity: ValidityThreats;
  effort: EffortShape;
}) {
  return (
    <Card
      title="Ameaças à validade"
      subtitle="Propriedades do conjunto de dados que qualificam todo o resto."
    >
      <dl className="def-grid">
        <dt>
          Sessões sem resposta do modelo
          <Hint term="noModelResponse" />
        </dt>
        <dd>
          {validity.sessions_without_model_response}
          {validity.sessions_without_model_response > 0 && (
            <span className="muted">
              {" "}
              — sem chamada à API, portanto sem custo a reportar
            </span>
          )}
        </dd>
        <dt>
          Transcripts auditáveis
          <Hint term="auditability" />
        </dt>
        <dd>
          {validity.transcripts_archived} arquivado(s)
          {validity.transcripts_unauditable > 0 ? (
            <span className="muted">
              {" "}
              — {validity.transcripts_unauditable} sessão(ões) perderam a origem e
              não podem mais ser auditadas
            </span>
          ) : (
            <span className="muted"> — nenhuma origem perdida</span>
          )}
        </dd>
        <dt>
          Versões do Claude Code
          <Hint term="claudeVersions" />
        </dt>
        <dd>
          {validity.claude_code_versions.length}
          {validity.claude_code_versions.length > 1 && (
            <span className="muted">
              {" "}
              — a ferramenta mudou durante a coleta; é variável de confusão
            </span>
          )}
        </dd>
        <dt>Modelos utilizados</dt>
        <dd>{validity.models_used.join(", ") || UNKNOWN}</dd>
        <dt>
          Sessões que editaram via shell
          <Hint term="shellEditing" />
        </dt>
        <dd>
          {validity.sessions_editing_via_shell}
          {validity.sessions_editing_via_shell > 0 && (
            <span className="muted"> — arquivos não rastreados no transcript</span>
          )}
        </dd>
        <dt>
          Sessões provavelmente retomadas
          <Hint term="resumedSessions" />
        </dt>
        <dd>
          {validity.sessions_likely_resumed}
          {validity.sessions_likely_resumed > 0 && (
            <span className="muted"> — consumo atribuído à sessão de origem</span>
          )}
        </dd>
        <dt>
          Sessões interrompidas
          <Hint term="interrupted" />
        </dt>
        <dd>{validity.interrupted_sessions}</dd>
        <dt>
          Transcripts com linhas corrompidas
          <Hint term="corruptedLines" />
        </dt>
        <dd>
          {validity.transcripts_with_corrupted_lines}
          {validity.corrupted_lines > 0 && (
            <span className="muted"> ({validity.corrupted_lines} linha(s))</span>
          )}
        </dd>
      </dl>

      <h2 style={{ marginTop: 18 }}>Formato do esforço</h2>
      <p className="cell-sub" style={{ marginTop: 0 }}>
        Uma soma esconde se as horas foram uma maratona ou muitas sessões curtas.
      </p>
      <dl className="def-grid">
        <dt>
          Sessão mediana
          <Hint term="medianSession" />
        </dt>
        <dd>{formatDuration(effort.median_session_seconds)}</dd>
        <dt>Maior sessão</dt>
        <dd>{formatDuration(effort.longest_session_seconds)}</dd>
        <dt>
          Concentração
          <Hint term="concentration" />
        </dt>
        <dd>
          {percent(effort.top_quintile_share, 0)} do tempo no quinto mais longo
        </dd>
        <dt>Prompts por sessão</dt>
        <dd>
          mediana {effort.median_prompts_per_session ?? UNKNOWN} · máximo{" "}
          {effort.max_prompts_in_a_session ?? UNKNOWN}
        </dd>
      </dl>
    </Card>
  );
}

export function SurveyCard({
  survey,
  sessions,
}: {
  survey: SurveyAggregate;
  sessions: number;
}) {
  const scales: Array<[string, number | null, string, HintKey]> = [
    ["Qualidade percebida", survey.mean_perceived_quality, "1 ruim · 5 ótima", "perceivedQuality"],
    ["Produtividade percebida", survey.mean_perceived_productivity, "1 baixa · 5 alta", "perceivedProductivity"],
    ["Esforço mental percebido", survey.mean_perceived_effort, "1 leve · 5 pesado", "perceivedEffort"],
    ["Confiança na saída", survey.mean_trust_in_output, "1 nenhuma · 5 total", "trust"],
  ];

  return (
    <Card
      title="Percepção declarada"
      term="perceivedQuality"
      subtitle="Respondido pelo desenvolvedor — nem observado, nem inferido."
    >
      {survey.responses === 0 ? (
        <p className="empty">
          Nenhuma resposta registrada. Percepção de qualidade não está em nenhum
          transcript: registre com{" "}
          <code className="mono">app.cli.main survey &lt;SESSION_ID&gt; --quality 4 …</code>
        </p>
      ) : (
        <>
          <p className="cell-sub" style={{ marginTop: 0 }}>
            {survey.responses} de {sessions} sessões respondidas
            {survey.responses < 5 && (
              <strong> — n pequeno demais para média ser conclusiva</strong>
            )}
            .
          </p>
          <div className="table-wrap">
            <table>
              <tbody>
                {scales.map(([label, value, scale, term]) => (
                  <tr key={label}>
                    <td>
                      {label}
                      <Hint term={term} />
                    </td>
                    <td className="num">{value ?? UNKNOWN}</td>
                    <td className="cell-sub">{scale}</td>
                  </tr>
                ))}
                <tr>
                  <td>
                    Retrabalho declarado
                    <Hint term="reworkReported" />
                  </td>
                  <td className="num">{survey.rework_reported_count}</td>
                  <td className="cell-sub">sessões</td>
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

export function BaselineCard({ baseline }: { baseline: Baseline | null }) {
  if (baseline === null) {
    return (
      <Card
        title="Linha de base (antes da IA)"
        term="baseline"
        subtitle="Sem janela de comparação, produtividade não tem denominador."
      >
        <p className="empty">
          Não calculada. Rode{" "}
          <code className="mono">app.cli.main baseline --project-id N</code>.
        </p>
      </Card>
    );
  }

  const rows: Array<[string, number, number, HintKey?]> = [
    ["Commits", baseline.before_commits, baseline.after_commits],
    ["Linhas adicionadas", baseline.before_insertions, baseline.after_insertions],
    ["Linhas removidas", baseline.before_deletions, baseline.after_deletions],
    ["Dias ativos", baseline.before_active_days, baseline.after_active_days, "activeDays"],
    ["Autores", baseline.before_authors.length, baseline.after_authors.length],
  ];
  const beforeRate = baseline.rates.before_commits_per_active_day ?? null;
  const afterRate = baseline.rates.after_commits_per_active_day ?? null;

  return (
    <Card
      title="Linha de base (antes da IA)"
      term="adoptionDate"
      subtitle={`Adoção em ${formatDate(baseline.adoption_at)} · janelas de ${baseline.window_days} dias`}
    >
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Métrica</th>
              <th className="num">Antes</th>
              <th className="num">Depois</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, before, after, term]) => (
              <tr key={label}>
                <td>
                  {label}
                  {term ? <Hint term={term} /> : null}
                </td>
                <td className="num">{formatNumber(before)}</td>
                <td className="num">{formatNumber(after)}</td>
              </tr>
            ))}
            <tr>
              <td className="cell-strong">
                Commits por dia ativo
                <Hint term="commitsPerActiveDay" />
              </td>
              <td className="num cell-strong">
                {beforeRate !== null ? beforeRate.toFixed(2) : UNKNOWN}
              </td>
              <td className="num cell-strong">
                {afterRate !== null ? afterRate.toFixed(2) : UNKNOWN}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p className="cell-sub" style={{ marginTop: 10 }}>
        Janelas de tamanhos diferentes tornam totais incomparáveis; compare as
        taxas por dia ativo.
      </p>
      <ul className="cell-sub" style={{ margin: "10px 0 0", paddingLeft: 18 }}>
        {baseline.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>
    </Card>
  );
}
