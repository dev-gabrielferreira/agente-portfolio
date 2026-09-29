---
name: logs
description: "Mostra o que o agente está fazendo agora num job (resumo do log)"
argument-hint: "[job]"
disable-model-invocation: true
---
Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo. Ou o job em `$ARGUMENTS`.

`log_do_job` (tamanho 6000) do job rodando ou do último. Resuma em até 6 linhas: fase, o que está
fazendo, erros e se precisa de algo do Gabriel. Log inteiro só se ele pedir.
