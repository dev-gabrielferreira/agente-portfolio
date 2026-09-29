---
name: plano-tecnico
description: Como escrever o plano técnico (docs/PLAN.md), a pesquisa de alternativas e os ADRs a partir da spec — arquitetura justificada, modelo de dados, contratos, costuras de teste e riscos. Use na tarefa de plano do planner e ao revisar arquitetura.
---

# Plano técnico

A spec diz o quê e por quê; o plano diz **como** — e por que assim e não de outro jeito. Inspirado
no Spec-Driven Development (github/spec-kit): cada decisão fica escrita, com as alternativas, para
que o builder, o revisor e o Gabriel entendam e para que a próxima sessão não reinvente.

## `docs/PLAN.md`

1. **Resumo** — 3–6 frases: o que será construído e a forma geral da solução.
2. **Arquitetura** — componentes e como conversam (um diagrama ASCII ou Mermaid simples ajuda),
   onde roda cada coisa dentro do container, jobs agendados, integrações externas.
3. **Pesquisa e decisões** — para cada escolha relevante (framework web, banco, frontend, fila,
   biblioteca de gráficos, provedor de LLM…): opções consideradas, critério, escolha, fonte
   (documentação via Context7, página oficial). Decisão difícil de reverter → ADR.
4. **Modelo de dados** — entidades com campos principais, relações, índices, retenção, migrações.
5. **Contratos** — rotas HTTP (método, caminho, entrada, saída, erros), eventos, formatos de
   arquivo. É o que o test-engineer usa para escrever os testes de aceitação.
6. **Costuras de teste** — onde os testes vão tocar o sistema (ex.: "regra de tarifação é função
   pura em `domain/tarifa.py`; API testada pelo TestClient; fluxo de upload pelo navegador").
   Prefira poucas costuras, altas e estáveis. Aponte a pasta da regra de negócio (vira
   `backend.domain` no manifesto: é onde o mutation testing mede).
7. **Checagem do contrato da plataforma** — container único, porta 8000, `/health`, `/data`,
   config por ambiente, cabe num VPS pequeno (memória/CPU do projeto: ~512 MB, 1 CPU).
8. **Riscos e mitigação.**

## ADRs (`docs/adr/NNNN-titulo.md`)

Formato em `docs/adr/README.md`. Escreva ADR só se a decisão for difícil de reverter, surpreendente
para quem chega depois, ou uma troca real. Três a seis ADRs num projeto novo é o normal.

## Critérios de uma boa arquitetura de portfólio

- **Adequada ao problema**: dados tabulares com análise → DuckDB/Polars pode vencer um ORM; tempo
  real → SSE/WebSocket; interface rica → SPA; conteúdo → renderização no servidor. Nada de
  "sempre FastAPI + React" por hábito — nem o contrário.
- **Demonstra engenharia**: uma ou duas decisões interessantes e bem explicadas valem mais que dez
  tecnologias.
- **Operável**: sobe com um `docker run`, sem serviço pago obrigatório, com falhas de integração
  externa tratadas.
- **Testável** pelas costuras definidas.

## Autoverificação antes de entregar

- Cada FR da spec aparece em algum componente/contrato e em algum ticket.
- Nenhuma escolha sem motivo; nenhuma dependência que a spec não justifica.
- Manifesto `.harness/stack.json` coerente com o plano (skill `arquitetura-livre`).
