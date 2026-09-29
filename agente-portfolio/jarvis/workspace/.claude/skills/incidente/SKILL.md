---
name: incidente
description: "Abre um job de incidente: algo quebrado em produção"
argument-hint: "<o que quebrou>"
disable-model-invocation: true
---
O que quebrou: $ARGUMENTS · Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo.

1. `projeto` (saúde, versão) e, se ajudar, `log_do_job` do último deploy.
2. Se a produção estiver fora do ar e a versão anterior era boa, ofereça primeiro `/rollback`.
3. Escreva o pedido: sintoma, desde quando, como reproduzir, resultado esperado. Abra com
   `mudar_projeto` tipo `incidente`.
