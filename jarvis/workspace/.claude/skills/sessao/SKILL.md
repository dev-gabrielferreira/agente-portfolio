---
name: sessao
description: "Abre uma sessão do Claude Code em segundo plano no laboratório para uma tarefa longa"
argument-hint: "<tarefa>"
disable-model-invocation: true
---
Tarefa: $ARGUMENTS

Siga a skill `sessoes`: escreva uma tarefa autossuficiente (objetivo, contexto, limites, formato e
local da entrega em `entregas/`), escolha o subagente se couber (`pesquisador`,
`analista-de-projeto`, `prototipador`), anexe o projeto desta conversa em modo leitura quando for
sobre ele, e crie com `nova_sessao` (Opus por padrão). Diga como acompanhar.
