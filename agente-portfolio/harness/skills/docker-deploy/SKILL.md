---
name: docker-deploy
description: Como escrever Dockerfile e configuração para o contrato de deploy do agente (porta 8000, /health, /data, não-root, imagem pequena, build multi-stage para front). Use ao mexer em Dockerfile, dependências de sistema ou arquivos estáticos.
---

# Docker e contrato de deploy

- Base `python:3.12-slim`; para SPA, estágio `node:22-slim` que roda `npm ci && npm run build` e
  copia só o `dist/` para a imagem final.
- Ordem das camadas para cache: `pyproject.toml` → `pip install .` → código.
- Usuário não-root (`USER app`), `/data` criado e com dono `app`. Nada é escrito fora de `/data`
  (e `/tmp`).
- `HEALTHCHECK` chamando `/health` (o orquestrador espera o estado `healthy`).
- `CMD` com `uvicorn ... --proxy-headers --forwarded-allow-ips "*"` (o Caddy está na frente).
- Dependências de sistema só as necessárias, com `--no-install-recommends` e limpeza do apt.
- `.dockerignore` exclui `.git`, `.venv`, `tests`, `.harness`, `.claude`, `data`, `.env*`.
- A imagem roda com `cap_drop: ALL`, `no-new-privileges` e 512 MB de memória: não dependa de
  capabilities nem carregue arquivos gigantes na memória.
- Você não roda Docker. O orquestrador faz o build e te devolve o log se falhar; teste localmente
  com `uvicorn` e `APP_ENV=staging DATA_DIR=$(mktemp -d)`.
