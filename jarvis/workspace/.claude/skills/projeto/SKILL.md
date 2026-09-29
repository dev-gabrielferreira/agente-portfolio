---
name: projeto
description: "Ficha completa de um projeto: o que é, onde está, decisões, features e jobs"
argument-hint: "[slug]"
disable-model-invocation: true
---
Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo.

1. `nota_do_projeto` e `projeto` (numa busca só: `select:mcp__agente__nota_do_projeto,mcp__agente__projeto`).
2. Mostre: o que é (1 linha), produção e staging (links), stack, decisões principais (ADRs),
   features passando/pendentes, último job e o que ele espera, armadilhas conhecidas.
3. Na conversa central, a partir daqui trate os próximos pedidos como sendo deste projeto até o
   Gabriel citar outro (e lembre que ele tem a conversa própria no app: "Jarvis · <Projeto>").
