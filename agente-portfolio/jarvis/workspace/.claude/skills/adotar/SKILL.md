---
name: adotar
description: "Traz para o agente um projeto que já existe no GitHub (inclusive um já no ar no VPS)"
argument-hint: "<url do repositório> [instruções]"
disable-model-invocation: true
---
Argumentos: $ARGUMENTS

Siga a seção "Projeto que ainda não é do agente" da skill `manutencao`: confirme nome, URL e o
domínio atual; pergunte o que não pode mudar e as variáveis de ambiente necessárias; abra com
`adotar_projeto`. Se o projeto já responde num domínio servido pelo seu Caddyfile principal,
avise que antes do deploy de produção será preciso tirar o bloco antigo — e ofereça fazer isso
com `/caddy` na hora certa.
