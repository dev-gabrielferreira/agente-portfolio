---
name: retomar
description: "Retoma um job pausado (em produção pede o código)"
argument-hint: "[job] [código]"
disable-model-invocation: true
---
Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo. Argumentos: $ARGUMENTS. Ache o job pausado e chame `retomar_job` (com `codigo` se a fase for
produção; peça se não veio).
