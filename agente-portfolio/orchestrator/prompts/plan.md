# Tarefa: plano técnico — o COMO (planner, Tarefa C)

Tipo de trabalho: **$job_type**

## Pedido do Gabriel

$request

## Ajustes pedidos pelo Gabriel na versão anterior da spec/plano

$feedback

## Problemas apontados pela validação automática (resolva antes de qualquer outra coisa)

$validation

## Starters disponíveis (pontos de partida opcionais, copiados antes do build)

$starters

## O que fazer

A spec aprovada pela descoberta está em `SPEC.md` (+ `CONTEXT.md` e `.harness/features.json`).
Decida **como** construir, seguindo as skills `plano-tecnico`, `arquitetura-livre` e
`tickets-verticais`. Não existe stack obrigatória: escolha a arquitetura que melhor serve a ESTE
produto dentro do contrato da plataforma, e justifique.

Entregue:

1. `docs/PLAN.md` — arquitetura, componentes, modelo de dados, contratos (rotas/eventos), pesquisa
   das alternativas (o que foi considerado e por que perdeu), costuras de teste, riscos.
2. `docs/adr/NNNN-*.md` — um ADR por decisão difícil de reverter (framework, banco, frontend,
   integração, autenticação…). Em manutenção, só as decisões novas.
3. `.harness/stack.json` — o manifesto que os gates, testes e deploy leem (formato na skill
   `arquitetura-livre`). Escolha um `starter` ou `"nenhum"`.
4. `.harness/tickets.json` + `.harness/tickets/<ID>-<slug>.md` — fatias verticais pequenas, cada
   uma cabendo numa sessão de contexto limpo, com `blocked_by` só onde há dependência real. Toda
   feature de `features.json` coberta por ao menos um ticket. Em manutenção, acrescente os tickets
   da mudança (ids novos) e mantenha os antigos como estão.

   **Cada ticket é o briefing completo de um builder que não viu nada desta conversa.** Use o
   modelo da skill `tickets-verticais`, com todas as seções: Objetivo, Contexto, O que construir
   (por camada: dados, regra, API, interface), Arquivos e módulos (caminhos entre crases), Contratos
   (quando houver rota, evento ou formato), Critérios de aceite (checkboxes observáveis, no mínimo
   3, espelhando os aceites das features), Costuras de teste, Fora do escopo e Riscos e armadilhas.
   Nada de "…", "TBD" ou "a definir": decida, ou mova para Fora do escopo. O orquestrador confere a
   forma de cada ticket e devolve o plano para você corrigir se faltar algo.

Não escreva código de produção e não rode `git commit`: o orquestrador valida os arquivos e faz o
checkpoint. Resposta estruturada: resumo do plano, arquitetura, stack, starter,
tags, ADRs, tickets e riscos.

## Skills recomendadas para esta tarefa

$skills
