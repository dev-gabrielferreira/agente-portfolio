---
name: tickets
description: "Lista os tickets do projeto com status e o que cada um entrega"
argument-hint: "[projeto]"
disable-model-invocation: true
---
Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo.

Leia `/srv/projetos/<slug>/.harness/tickets.json` (status: todo, done, blocked; `attempts`) e o
objetivo de cada arquivo de ticket. Mostre uma linha por ticket: `T03 ✔ Cadastro de chillers — …`
(✔ feito, ▶ em construção = primeiro todo com dependências prontas, ⏸ pendente, ✖ bloqueado).
Bloqueado: diga o motivo (PROGRESS.md) e ofereça `/ajustar` ou um job de mudança.
