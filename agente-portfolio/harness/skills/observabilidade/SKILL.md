---
name: observabilidade
description: Logs estruturados, request id, métricas simples e health checks úteis para depurar produção e para o monitor do agente. Use ao criar o app, integrações externas, jobs agendados e tratamento de erros.
---

# Observabilidade

- **Logs estruturados em JSON** para stdout (o Docker guarda): `ts`, `level`, `logger`, `msg`,
  `request_id`, e campos do contexto. Nível por `LOG_LEVEL`.
- **Request id**: middleware lê `X-Request-ID` ou gera um; devolve no cabeçalho e põe em todo log
  da requisição. Erros 500 logam o request id e a stack; a resposta mostra só o id.
- **Log de integrações**: cada chamada externa registra destino, status, duração e tentativas —
  nunca o corpo com dados pessoais ou segredos.
- **Jobs agendados** registram início, fim, duração, linhas processadas e erro. Expõe
  `GET /api/jobs/status` com a última execução de cada job (o avaliador e você olham isso).
- **`/health`** é leve e não depende de serviços externos (o monitor chama a cada 5 min).
  Se precisar checar dependências, crie `/health/ready` separado.
- **Métricas mínimas** (opcional, útil em dashboards de dados): contador de requisições por rota e
  status, latência p50/p95 em memória, expostos em `/api/metrics` (JSON).
- **Mensagens de erro para humanos**: diga o que aconteceu e o que fazer ("A API da ONS não
  respondeu; mostrando dados de 24/09 às 14h"), nunca "Internal Server Error".
