---
name: arquitetura-livre
description: Contrato da plataforma, manifesto .harness/stack.json e starters — o que é fixo, o que é escolha, e como declarar a arquitetura para que gates, testes e deploy funcionem em qualquer stack. Use ao planejar a arquitetura, montar a fundação ou mudar a estrutura do projeto.
---

# Arquitetura livre, plataforma fixa

## Fixo (contrato da plataforma — o deploy depende disso)

- Um `Dockerfile` na raiz, imagem única, processo escutando `0.0.0.0:8000`, usuário não-root.
- `GET /health` → 200 `{"status": "ok"}` em < 1 s, sem depender de serviço externo.
- Dados persistentes só em `/data`; configuração só por variáveis de ambiente (`.env.example`).
- Testes do test-engineer em **pytest** (aceitação, e2e com Playwright, propriedades): por isso todo
  projeto tem `pyproject.toml` com o extra `dev` (pytest, pytest-cov, pytest-playwright,
  hypothesis, schemathesis, axe-playwright-python, mutmut, pyright, respx, ruff, pip-audit). O
  backend, quando existir, é Python — o framework é escolha sua.

## Escolha (decida no plano, registre em ADR)

Framework web (FastAPI, Litestar, Django, Flask…), servidor-renderizado vs SPA vs estático, banco
(SQLite, DuckDB, Postgres embutido não; arquivo em `/data`), frontend (HTMX, React, Vue, Svelte,
Astro, vanilla), bibliotecas de gráfico, filas internas, agendadores, provedores externos.

## O manifesto `.harness/stack.json`

É como a arquitetura conversa com os sensores. Gates, fixtures de teste, fuzz de contrato,
mutation testing e o detector de design leem daqui.

```json
{
  "version": 1,
  "starter": "fastapi-react",
  "summary": "FastAPI (API em /api) + React/Vite + SQLite em /data",
  "start": "python -m uvicorn app.main:app --host 127.0.0.1 --port {port}",
  "health": "/health",
  "backend": {
    "language": "python", "framework": "fastapi", "package": "app",
    "asgi": "app.main:app", "settings": "app.config:settings",
    "openapi": true, "domain": "app/services"
  },
  "frontend": {
    "dir": "frontend", "framework": "react", "package_manager": "npm",
    "lint": "npm run lint", "typecheck": "npm run typecheck", "test": "npm test", "build": "npm run build"
  },
  "ui_paths": ["frontend/src"],
  "database": "sqlite"
}
```

| Campo | Quem usa |
|---|---|
| `start` (com `{port}`), `health` | fixture `live_server` dos e2e (Playwright), avaliador |
| `backend.package` | cobertura mínima no gate |
| `backend.asgi`, `backend.settings` | fixtures `client`/`data_dir` e fuzz de contrato (Schemathesis) |
| `backend.openapi` | liga o fuzz de contrato (`tests/test_api_contract.py`) |
| `backend.domain` | pasta da regra de negócio pura: alvo obrigatório do mutation testing |
| `frontend.*` | gate do frontend: dependências, lint, tipos, testes, build; `.only/.skip` reprova |
| `ui_paths` | detector de anti-padrões de design (impeccable) |

- Sem backend (site estático/SPA pura): `"backend": null` e um `start` que sirva o build
  (ex.: `python -m http.server {port} --directory dist --bind 127.0.0.1`), com `/health` servido
  como arquivo `health` JSON. Cobertura Python fica desligada; os testes e2e continuam.
- Backend não-ASGI (Django WSGI, CLI, ETL): omita `asgi`/`openapi`; o test-engineer reescreve as
  fixtures para a arquitetura.
- Comandos do frontend nunca "sempre passam" (`|| true`, `echo`, `exit 0`): o orquestrador reprova.

## Starters

Atalhos opcionais, copiados pelo orquestrador **antes do primeiro ticket**, só para arquivos que
não existem. Escolha um quando a arquitetura do plano coincidir; senão `"starter": "nenhum"` e o T01
monta a fundação do zero seguindo o plano.

- `python-fastapi` — FastAPI + Jinja2/HTMX + SQLite, um container (receita `receita-python-fastapi`).
- `fastapi-react` — FastAPI com API em `/api` + React 19/TypeScript/Vite, testes Vitest, ESLint,
  build multi-stage no Dockerfile servindo a SPA (receita `receita-fastapi-react`).

Os manifestos de referência de cada starter estão em `.claude/starters/<nome>.json`.
