---
name: status
description: "Situação agora: o projeto desta conversa ou tudo o que espera o Gabriel"
argument-hint: "[projeto]"
disable-model-invocation: true
---
Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo. Na central sem argumento: visão geral.

1. Carregue numa busca só as ferramentas que vai usar (`select:mcp__agente__resumo,mcp__agente__projeto,mcp__agente__jobs`).
2. Visão geral: `resumo`. Projeto: `projeto` (e `jobs` com `projeto=<slug>` se precisar).
3. Responda em até 8 linhas: primeiro o que espera o Gabriel (com o comando para resolver, ex.:
   "`/aprovar deploy` com o código"), depois o que está rodando e em que fase, depois o que está fora
   do ar. Nada de listar o que está normal.
