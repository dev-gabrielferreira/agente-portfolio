---
name: novo
description: "Abre um projeto novo no pipeline e traz a primeira rodada de perguntas"
argument-hint: "<ideia do projeto>"
disable-model-invocation: true
---
Pedido do Gabriel: $ARGUMENTS

Siga a skill `novo-projeto`. Em resumo: entenda o pedido (se faltar o essencial — problema, público,
dados — pergunte UMA coisa, com recomendação), proponha um nome curto, abra com `novo_projeto` e,
quando o planner terminar a rodada 1, traga as perguntas numeradas com a recomendação de cada uma.
A conversa própria do projeto aparece no app em ~1 minuto ("Jarvis · <Nome>"): avise.
