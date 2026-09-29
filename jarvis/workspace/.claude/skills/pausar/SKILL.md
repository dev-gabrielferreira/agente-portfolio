---
name: pausar
description: "Pausa o job do projeto na próxima ação"
argument-hint: "[job]"
disable-model-invocation: true
---
Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo. Ou o job em `$ARGUMENTS`. Ache o job rodando/na fila e chame `pausar_job`. Diga como retomar (`/retomar`).
