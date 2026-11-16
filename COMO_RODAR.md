# Como rodar o projeto

Guia direto de setup e execução. Para arquitetura, decisões e o que cada tela
mostra, veja o [README](README.md).

## Pré-requisitos

| Ferramenta | Versão testada | Para quê |
|---|---|---|
| Docker + Docker Compose | qualquer recente | Postgres (e, opcionalmente, backend/frontend) |
| Python | 3.11+ | backend, CLI |
| Node.js | 20+ | frontend, e o `ccusage` (é um pacote npm) |
| `ccusage` | 20.x | fonte de custo real; opcional — sem ele o custo cai para o transcript |

Verifique o que já tem instalado:

```bash
docker --version
python3 --version
node --version && npm --version
ccusage --version   # se faltar: npm install -g ccusage
```

Este projeto lê os transcripts do Claude Code direto de `~/.claude` na sua
máquina — não precisa de nenhuma sessão de exemplo, ele usa o que você já tem.

---

## 1. Configurar o `.env`

```bash
cp .env.example .env
```

Abra o `.env` e ajuste se precisar — os padrões já funcionam para uso local:

- `CLAUDE_DATA_PATH=auto` — detecta `~/.claude` sozinho.
- `DATABASE_URL` — já aponta para a porta `5434` do Postgres do compose (ver
  `docker-compose.yml`).
- `AI_ANALYSIS_ENABLED=false` — deixe assim a menos que vá configurar uma
  chave de LLM (seção 6).

## 2. Subir o Postgres

```bash
docker compose up -d postgres
```

Se o Docker Desktop/daemon não estiver rodando, este comando falha com
`Cannot connect to the Docker daemon` — abra o Docker primeiro.

## 3. Backend (API + coleta)

```bash
cd backend
python3 -m venv .venv
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/alembic upgrade head        # cria as tabelas
```

Popule o banco com as sessões que já existem em `~/.claude`:

```bash
./.venv/bin/python -m app.cli.main doctor   # confirma que as 3 fontes (Claude Code, ccusage, git) foram encontradas
./.venv/bin/python -m app.cli.main sync     # grava/atualiza no Postgres
```

Suba a API:

```bash
./.venv/bin/uvicorn app.main:app --reload
```

→ http://localhost:8000/docs (Swagger, para explorar os endpoints)

Mantenha este terminal aberto; abra um novo para o frontend.

## 4. Frontend (dashboard)

Em outro terminal, na raiz do projeto:

```bash
cd frontend
npm install
npm run dev
```

→ http://localhost:3000

O frontend espera a API em `http://127.0.0.1:8000` por padrão (variável
`API_URL`, ver `frontend/lib/api.ts`). Se a API estiver noutra porta/host,
exporte `API_URL` antes de `npm run dev`.

---

## Alternativa: tudo via Docker

Sem instalar Python/Node localmente:

```bash
cp .env.example .env
docker compose up -d
```

Sobe Postgres + backend (`:8000`) + frontend (`:3000`) juntos. A coleta ainda
roda pela CLI, só que dentro do container:

```bash
docker compose exec backend python -m app.cli.main sync
```

Limitação: o `ccusage` é um CLI de Node que não vem na imagem do backend, então
dentro do Docker o custo sempre cai para o transcript. Para números de custo
reais, rode a coleta (`sync`) direto na máquina host (seção 3), não pelo
`docker compose exec`.

---

## 5. Rodar os testes

```bash
cd backend
./.venv/bin/python -m pytest
```

Roda em SQLite em memória — não depende do Postgres nem do `~/.claude` reais.
Dois arquivos são pulados automaticamente quando o ambiente não os suporta
(`test_integration_real_environment.py` precisa de dados reais na máquina,
`test_postgres_integration.py` precisa do Postgres de fato rodando).

Frontend:

```bash
cd frontend
npm run typecheck
npm run build
```

## 6. Habilitar a análise por LLM (opcional)

Desligada por padrão — a coleta objetiva funciona inteira sem isso. Para
ligar, no `.env`:

```bash
AI_ANALYSIS_ENABLED=true
LLM_PROVIDER=anthropic
LLM_API_KEY=...          # ou exporte ANTHROPIC_API_KEY
```

```bash
./.venv/bin/python -m app.cli.main analyze <SESSION_ID> --dry-run --show-digest  # veja o que seria enviado, sem enviar
./.venv/bin/python -m app.cli.main analyze <SESSION_ID>
```

---

## Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `Cannot connect to the Docker daemon` | Docker não está rodando | abra o Docker Desktop / `sudo systemctl start docker` |
| API sobe mas `/health` mostra banco fora do ar | Postgres não subiu, ou `DATABASE_URL` no `.env` não bate com a porta do compose (`5434`) | `docker compose ps`, confira a porta |
| `sync` não encontra sessões | `CLAUDE_DATA_PATH` errado, ou nenhuma sessão do Claude Code na máquina | rode `app.cli.main doctor` para diagnosticar |
| Dashboard mostra "API indisponível" | uvicorn não está rodando, ou `API_URL` do frontend aponta para o lugar errado | confira o terminal do backend; exporte `API_URL` se necessário |
| `ccusage` não encontrado | não está instalado globalmente | `npm install -g ccusage`, ou ajuste `CCUSAGE_BIN` no `.env` |
| Alembic reclama de migration pendente/divergente | banco desatualizado | `./.venv/bin/alembic upgrade head` |
