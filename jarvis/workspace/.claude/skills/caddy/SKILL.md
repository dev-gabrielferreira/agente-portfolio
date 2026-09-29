---
name: caddy
description: "Propõe (ou aplica/desfaz com o código) mudanças no Caddyfile principal do VPS"
argument-hint: "<mudança> | ver | aprovar N <código> <confirmação> | desfazer N <código> <confirmação> | rejeitar N"
disable-model-invocation: true
---
Pedido: $ARGUMENTS

Siga a skill `infra-caddy`. Em resumo: `ver` → `caddyfile` e explique os sites. Mudança → leia o
arquivo, proponha com `propor_mudanca_caddy` (edições mínimas), mostre o diff e os alertas e peça o
código do autenticador e a confirmação que chegou no celular dele. `aprovar N <código>
<confirmação>` → `aprovar_mudanca_caddy`. `desfazer N <código> <confirmação>` →
`desfazer_mudanca_caddy`. `rejeitar N` → `rejeitar_mudanca_caddy`.
