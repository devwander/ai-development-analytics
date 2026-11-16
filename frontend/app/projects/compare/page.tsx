import { PeriodFilter, readPeriod } from "@/components/PeriodFilter";
import { Card, Notice, Th } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatCost, formatNumber, UNKNOWN } from "@/lib/format";

export const dynamic = "force-dynamic";

function percent(value: number | null, digits = 0): string {
  if (value === null) return UNKNOWN;
  return `${(value * 100).toFixed(digits)}%`;
}

function decimal(value: number | null, digits = 1): string {
  if (value === null) return UNKNOWN;
  return value.toFixed(digits).replace(".", ",");
}

export default async function CompareProjectsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const period = readPeriod(await searchParams);

  let rows;
  try {
    rows = await api.projectComparison(period);
  } catch (error) {
    return (
      <>
        <h1>Comparação entre projetos</h1>
        <Notice variant="error">
          <strong>API indisponível.</strong>{" "}
          {error instanceof ApiError ? error.message : "Erro inesperado."}
        </Notice>
      </>
    );
  }

  const comparable = rows.filter((row) => row.sessions > 1);
  const single = rows.length - comparable.length;

  return (
    <>
      <a className="back-link" href="/projects">
        ← Projetos
      </a>
      <h1>Comparação entre projetos</h1>
      <p className="page-note">
        Totais não se comparam entre projetos de tamanhos diferentes; taxas sim.
        As colunas normalizadas estão à direita. <strong>Atribuição</strong> é o
        teto do que passou por edição rastreada da IA e{" "}
        <strong>cobertura</strong> é a fração de sessões com commit associado —
        as duas dizem o quanto as colunas de entrega podem ser levadas a sério.
      </p>

      <PeriodFilter
        basePath="/projects/compare"
        since={period.since}
        until={period.until}
        invalid={period.invalid}
        matchedSessions={rows.reduce((sum, row) => sum + row.sessions, 0)}
      />

      <Card
        title={`${rows.length} projeto(s)`}
        subtitle={
          single > 0
            ? `${single} projeto(s) com uma única sessão aparecem no fim: uma sessão não é uma amostra.`
            : undefined
        }
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Projeto</th>
                <Th numeric term="sessions">Sessões</Th>
                <Th numeric term="activeTurn">Horas</Th>
                <Th numeric term="promptsPerHour">Prompts/h</Th>
                <Th numeric term="costPerHour">US$/h</Th>
                <Th numeric term="toolErrors">Erro em tools</Th>
                <Th numeric term="linkedCommits">Commits</Th>
                <Th numeric term="attribution">Atribuição</Th>
                <Th numeric term="coverage">Cobertura</Th>
                <Th numeric term="perceivedQuality">Qual. percebida</Th>
              </tr>
            </thead>
            <tbody>
              {[...comparable, ...rows.filter((r) => r.sessions <= 1)].map((row) => (
                <tr key={row.id}>
                  <td>
                    <a href={`/projects/${row.id}`} className="cell-strong">
                      {row.name}
                    </a>
                  </td>
                  <td className="num">{row.sessions}</td>
                  <td className="num">{decimal(row.active_turn_hours)}</td>
                  <td className="num">{decimal(row.prompts_per_hour)}</td>
                  <td className="num">
                    {row.cost_per_hour !== null ? formatCost(row.cost_per_hour) : UNKNOWN}
                  </td>
                  <td className="num">{percent(row.tool_error_rate, 1)}</td>
                  <td className="num">{formatNumber(row.linked_commits)}</td>
                  <td className="num">{percent(row.attribution_ratio)}</td>
                  <td className="num">{percent(row.commit_coverage)}</td>
                  <td className="num">
                    {row.mean_perceived_quality !== null ? (
                      <>
                        {decimal(row.mean_perceived_quality, 1)}
                        <span className="muted"> (n={row.survey_responses})</span>
                      </>
                    ) : (
                      UNKNOWN
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="cell-sub" style={{ marginTop: 14 }}>
          <strong>US$/h</strong> mede o custo de manter o contexto, não o volume
          produzido: a saída gerada é uma fração mínima dos tokens, e o resto é
          leitura de cache. <strong>Prompts/h</strong> é densidade de interação —
          um valor alto pode ser diálogo produtivo ou dificuldade de alinhamento,
          e distinguir os dois exige a percepção declarada.
        </p>
      </Card>
    </>
  );
}
