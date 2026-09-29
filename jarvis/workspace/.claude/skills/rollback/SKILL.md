---
name: rollback
description: "Volta a produção do projeto para a versão anterior (pede o código)"
argument-hint: "[código]"
disable-model-invocation: true
---
Argumentos: $ARGUMENTS · Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo.

1. `projeto`: mostre a versão atual e a anterior (`versao_anterior`). Sem anterior, não há rollback.
2. Código de 6 dígitos: dos argumentos ou peça. `rollback` com slug e código.
3. Diga a versão que ficou no ar e sugira `/incidente` para corrigir a causa.
