---
name: receita-python-fastapi
description: Receita do starter python-fastapi — FastAPI + Jinja2/HTMX + SQLite em /data num container, estrutura de pastas e convenções. Use quando o plano escolheu esse starter ou uma arquitetura FastAPI server-rendered.
---

# Receita: FastAPI + HTMX (starter python-fastapi)

Estrutura do starter. É um ponto de partida: siga o plano (docs/PLAN.md) quando ele divergir.

```
app/
  main.py          # cria o FastAPI, monta rotas, /health, arquivos estáticos
  config.py        # Settings (pydantic-settings) lidos do ambiente
  db.py            # engine/sessão; SQLite em /data/app.db por padrão
  models.py        # SQLAlchemy 2.0 (Mapped[...])
  schemas.py       # Pydantic de entrada/saída
  routes/          # um módulo por recurso (APIRouter), finos
  services/        # REGRA DE NEGÓCIO pura, sem HTTP — backend.domain, alvo do mutation testing
  templates/ static/   # interface server-side (Jinja2 + HTMX) usando design/tokens.css
design/            # do designer: DESIGN.md, tokens.css/json, mockups (não edite)
tests/
  unit/            # seus testes (builder)
  acceptance/ e2e/ properties/ test_api_contract.py   # do test-engineer (não edite)
scripts/check.sh test_quality.py mutation.sh   # gates do harness (não edite)
Dockerfile  .dockerignore  pyproject.toml  .env.example  README.md
docs/PLAN.md docs/adr/   # plano técnico e ADRs (do planner)
```

## Regras

- Python 3.12, dependências no `pyproject.toml` (`[project.dependencies]` e grupo `dev`).
  Ambiente local: `python -m venv .venv && .venv/bin/pip install -e '.[dev]'`.
- Rodar localmente: `.venv/bin/uvicorn app.main:app --port 8000` (em background para testar,
  e **encerre o processo** quando terminar).
- Rotas finas; lógica em `services/` (é ali que o mutation testing mede se os testes pegam bugs).
  Tipagem em tudo que é público — o Pyright roda no gate e os diagnósticos chegam a você a cada edição.
- Interface: copie `design/tokens.css` para `app/static/` e use só esses tokens.
- Configuração: `class Settings(BaseSettings)` com defaults seguros para dev; `DATA_DIR=/data`.
- Banco: SQLite em `DATA_DIR`. Crie tabelas no startup (lifespan) ou use Alembic se houver
  migração de dados. Nunca apague dados existentes numa migração.
- Frontend: Jinja2 + HTMX + CSS próprio servido pelo FastAPI (um container só). Para SPA rica, o
  plano escolhe o starter `fastapi-react` (receita `receita-fastapi-react`).
- Tarefas periódicas: APScheduler dentro do app, com trava para não rodar duas vezes.
- `README.md` do projeto é vitrine: o que é, screenshot, como funciona (diagrama simples),
  stack, decisões interessantes, como rodar. Escreva para um recrutador técnico.
- Erros: handler global que loga o detalhe e devolve mensagem genérica ao usuário.
