---
name: cancelar
description: "Cancela um job (pede confirmação no celular)"
argument-hint: "[job]"
disable-model-invocation: true
---
Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo. Ou o job em `$ARGUMENTS`. Mostre qual job, a fase e o que se perde; se ele confirmar, `cancelar_job`.
