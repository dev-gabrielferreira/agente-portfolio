---
name: aprovar
description: "Aprova a etapa que espera o Gabriel: spec, design ou deploy (deploy pede o código)"
argument-hint: "[spec|design|deploy] [código]"
disable-model-invocation: true
---
Argumentos: $ARGUMENTS · Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo.

Siga a skill `aprovacoes`:
1. Ache o job esperando aprovação (`projeto`/`resumo`). Se a etapa não veio, deduza pela espera.
2. Antes de aprovar, mostre o essencial (spec/plano: resumo; design: conceito e link; deploy: o que
   muda, link do staging, vereditos e achados). Achado "blocker" → recomende NÃO aprovar.
3. Deploy: precisa do código de 6 dígitos. Se veio nos argumentos, use; senão peça. Nunca repita um
   código recusado.
4. `aprovar` com a etapa (e o `codigo` no deploy). Diga o resultado e o próximo passo.
