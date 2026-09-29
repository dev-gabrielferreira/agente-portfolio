---
name: ajustar
description: "Pede ajustes na etapa que está esperando o Gabriel (perguntas, spec, plano, design ou deploy)"
argument-hint: "<o que mudar>"
disable-model-invocation: true
---
Ajuste pedido: $ARGUMENTS · Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo.

1. Ache o job esperando o Gabriel.
2. Reescreva o pedido de forma verificável (o que mudar, como saber que ficou certo, o que não
   mexer) e mostre em 2–4 linhas.
3. `pedir_ajustes` com esse texto (o celular pede confirmação). Diga para qual fase o job volta.
