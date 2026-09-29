---
name: responder
description: "Envia as respostas do Gabriel às perguntas de descoberta"
argument-hint: "<1 ok, 2 só a equipe, 3 …>"
disable-model-invocation: true
---
Respostas do Gabriel: $ARGUMENTS

1. Ache o job esperando `answers` (projeto desta conversa, ou pergunte qual) e leia as perguntas com `job`.
2. Monte `{id_da_pergunta: resposta}` pela numeração que você mostrou: "ok"/"pode"/"segue" = a
   recomendação do planner; o que ele não respondeu também vai com a recomendação (diga isso).
3. Mostre o mapeamento em uma linha por pergunta e envie com `responder_perguntas` (o celular pede
   confirmação). Diga o que acontece agora (nova rodada ou spec).
