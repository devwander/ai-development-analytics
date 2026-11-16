import { PeriodFilter, readPeriod } from "@/components/PeriodFilter";
import { Card, Notice, Th } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { formatCost, formatDate, formatHours, formatTokens } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function ProjectsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const period = readPeriod(await searchParams);

  let projects;
  try {
    projects = await api.projects(period);
  } catch (error) {
    return (
      <>
        <h1>Projetos</h1>
        <Notice variant="error">
          <strong>API indisponível.</strong>{" "}
          {error instanceof ApiError ? error.message : "Erro inesperado."}
        </Notice>
      </>
    );
  }

  return (
    <>
      <h1>Projetos</h1>
      <p className="page-note">
        O caminho do projeto vem da raiz do repositório Git quando existe, e do
        diretório comum a todos os <code className="mono">cwd</code> da sessão caso
        contrário — nunca do nome do diretório do Claude Code, que é uma codificação
        com perda.
      </p>

      <PeriodFilter
        basePath="/projects"
        since={period.since}
        until={period.until}
        invalid={period.invalid}
        matchedSessions={projects.reduce((sum, p) => sum + p.sessions, 0)}
      />

      <p style={{ marginTop: -14, marginBottom: 20 }}>
        <a href="/projects/compare" style={{ textDecoration: "underline" }}>
          Comparar projetos lado a lado →
        </a>
      </p>

      <Card
        title={`${projects.length} projeto(s)`}
        subtitle="Ordenados por custo equivalente. Abra um projeto para os mesmos indicadores da visão geral, recortados. Custo n/d significa que nenhuma fonte pôde determiná-lo."
      >
        {projects.length === 0 ? (
          <p className="empty">
            Nenhum projeto sincronizado. Rode{" "}
            <code className="mono">python -m app.cli.main sync</code>.
          </p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <Th term="projectPath">Projeto</Th>
                  <Th numeric term="sessions">Sessões</Th>
                  <Th numeric term="activeTurn">Trabalho</Th>
                  <Th numeric term="tokens">Tokens</Th>
                  <Th numeric term="cost">Custo eq.</Th>
                  <th>Período</th>
                  <th>Ver</th>
                </tr>
              </thead>
              <tbody>
                {projects.map((project) => (
                  <tr key={project.id}>
                    <td>
                      <a href={`/projects/${project.id}`} className="cell-strong">
                        {project.name}
                      </a>
                      <div className="cell-sub mono">{project.path}</div>
                      {project.repository_url ? (
                        <div className="cell-sub">{project.repository_url}</div>
                      ) : (
                        <div className="cell-sub">sem repositório Git</div>
                      )}
                    </td>
                    <td className="num">{project.sessions}</td>
                    <td className="num">{formatHours(project.active_turn_seconds)}</td>
                    <td className="num">{formatTokens(project.total_tokens)}</td>
                    <td className="num">{formatCost(project.cost_usd)}</td>
                    <td className="cell-sub">
                      {formatDate(project.first_session_at)}
                      {" → "}
                      {formatDate(project.last_session_at)}
                    </td>
                    <td className="cell-sub">
                      <a href={`/projects/${project.id}`}>indicadores</a>
                      {" · "}
                      <a href={`/sessions?project_id=${project.id}`}>sessões</a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
