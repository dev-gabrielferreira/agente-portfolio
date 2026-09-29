---
name: api-design
description: Convenções de API HTTP para os projetos (FastAPI) — recursos, status codes, erros no formato Problem Details, paginação, filtros, versionamento e OpenAPI útil. Use ao criar ou alterar qualquer rota.
---

# Desenho de API

- **Recursos no plural**, substantivos: `GET /api/readings`, `POST /api/readings`,
  `GET /api/readings/{id}`. Ações que não são CRUD viram sub-recurso: `POST /api/pipeline/runs`.
- **Rotas estáticas antes das com parâmetro** (`/items/export` antes de `/items/{id}`).
- **Status codes**: 200 leitura/atualização, 201 criação (com `Location`), 204 sem corpo,
  400 requisição malformada, 401 sem autenticação, 403 sem permissão, 404 não existe,
  409 conflito/duplicado, 422 validação, 429 limite, 500 nunca de propósito.
- **Erros** no formato Problem Details (RFC 9457): `{"type","title","status","detail","instance"}`,
  com handler global. Mensagem útil para o usuário, detalhe técnico só no log.
- **Schemas separados** de entrada (`ItemCreate`), atualização (`ItemUpdate`, campos opcionais) e
  saída (`ItemOut`). Nunca devolva o modelo do banco direto. Valide limites no schema
  (`Field(min_length=1, max_length=200)`, `ge=0`).
- **Paginação** em toda listagem que pode crescer: `?limit=50&offset=0` (limite máximo no servidor)
  e resposta `{"items": [...], "total": n, "limit": 50, "offset": 0}`.
- **Filtros e ordenação** explícitos e validados (`?sort=-created_at`, lista branca de campos).
- **Datas** em ISO 8601 com fuso (`2026-09-24T18:50:00-03:00`); armazene em UTC.
- **Idempotência**: PUT e DELETE idempotentes; POST sensível a repetição aceita `Idempotency-Key`.
- **OpenAPI** é documentação e contrato de teste (o fuzz do Schemathesis lê daqui): dê `summary`,
  `response_model` e `responses` de erro a cada rota. Mantenha `/docs` ativo — é vitrine.
- **Versão**: prefixe `/api/v1` se o projeto tiver consumidores externos; senão `/api`.
