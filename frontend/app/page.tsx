import {
  DailyCharts,
  KpiRows,
  MissingUsageNotice,
  StatusAndCategories,
} from "@/components/OverviewPanels";
import {
  AttributionCard,
  SurveyCard,
  ValidityCard,
} from "@/components/ResearchPanels";
import { PeriodFilter, readPeriod } from "@/components/PeriodFilter";
import { Notice } from "@/components/ui";
import { ApiError, api } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function OverviewPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const period = readPeriod(await searchParams);

  let overview;
  try {
    overview = await api.overview(period);
  } catch (error) {
    const message =
      error instanceof ApiError ? error.message : "Erro inesperado ao consultar a API.";
    return (
      <>
        <h1>Visão geral</h1>
        <Notice variant="error">
          <strong>API indisponível.</strong> {message}
          <br />
          Suba o backend com <code className="mono">uvicorn app.main:app</code> e o
          PostgreSQL com <code className="mono">docker compose up -d postgres</code>.
        </Notice>
      </>
    );
  }

  return (
    <>
      <h1>Visão geral</h1>
      <p className="page-note">
        Tudo nesta página é <strong>dado observado</strong>, coletado dos transcripts do
        Claude Code, do ccusage e do Git. A única seção inferida por IA está marcada
        como tal. Para os mesmos números recortados por projeto, abra um projeto em{" "}
        <a href="/projects" style={{ textDecoration: "underline" }}>
          Projetos
        </a>
        .
      </p>

      <PeriodFilter
        basePath="/"
        since={period.since}
        until={period.until}
        invalid={period.invalid}
        matchedSessions={overview.sessions}
      />

      <KpiRows overview={overview} />

      {overview.attribution && overview.coverage && (
        <div className="section grid-2">
          <AttributionCard
            attribution={overview.attribution}
            coverage={overview.coverage}
          />
          {overview.validity && overview.effort_shape && (
            <ValidityCard
              validity={overview.validity}
              effort={overview.effort_shape}
            />
          )}
        </div>
      )}

      {overview.survey && (
        <div className="section">
          <SurveyCard survey={overview.survey} sessions={overview.sessions} />
        </div>
      )}

      <DailyCharts overview={overview} />
      <StatusAndCategories
        overview={overview}
        categoriesEmptyMessage="Nenhuma sessão analisada ainda. Habilite AI_ANALYSIS_ENABLED e rode a análise."
      />
      <MissingUsageNotice overview={overview} />
    </>
  );
}
