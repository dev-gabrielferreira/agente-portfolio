---
name: mudar
description: "Abre um job de mudança num projeto (inclusive em produção) com critérios de aceite"
argument-hint: "<o que mudar>"
disable-model-invocation: true
---
Pedido: $ARGUMENTS · Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo.

Siga a skill `manutencao`: contexto pela nota do projeto, uma pergunta por vez se faltar algo
essencial, e um pedido com problema/objetivo, critérios de aceite verificáveis (entrada →
resultado), o que NÃO mudar e pistas do código. Mostre o pedido e abra com `mudar_projeto`
(tipo `mudanca`). Mudança em projeto nunca é feita por você direto.
