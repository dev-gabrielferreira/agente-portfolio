---
name: ajuda
description: "Lista os comandos do Jarvis e o que cada um faz"
disable-model-invocation: true
---
Mostre ao Gabriel esta lista, do jeito que está (é para tela de celular):

**Acompanhar** · `/status [projeto]` situação · `/projeto [slug]` ficha completa · `/tickets` tickets e
andamento · `/logs [job]` o que o agente está fazendo · `/perguntas` perguntas pendentes

**Decidir** · `/responder 1 ok, 2 …` respostas · `/plano` spec + plano + tickets para aprovar ·
`/aprovar [spec|design|deploy] [código]` · `/ajustar <o que mudar>` · `/rollback [código]`

**Pedir** · `/novo <ideia>` projeto novo · `/mudar <o que>` mudança · `/incidente <o que quebrou>` ·
`/adotar <repo> [instruções]` trazer um projeto existente · `/caddy <mudança>` no Caddyfile principal

**Controlar** · `/pausar` · `/retomar [código]` · `/cancelar`

**Pesquisar** · `/pesquisar <tema>` com fontes · `/sessao <tarefa>` sessão em segundo plano no laboratório

Nesta conversa de projeto, os comandos sem argumento valem para o projeto dela. Deploy e rollback
pedem o código de 6 dígitos do autenticador; mudanças no Caddyfile pedem também a confirmação que
chega no celular.
