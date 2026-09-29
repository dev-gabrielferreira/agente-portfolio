---
name: perguntas
description: "Mostra as perguntas de descoberta pendentes, com a recomendação de cada uma"
argument-hint: "[job]"
disable-model-invocation: true
---
Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo. Ou o job em `$ARGUMENTS`.

1. Ache o job esperando `answers` (`projeto` ou `resumo`) e leia com `job`.
2. Mostre as perguntas NUMERADAS, cada uma com a recomendação do planner e o porquê em meia linha.
3. Termine com: "Responda com `/responder 1 ok, 2 …` (ok = recomendação)".
