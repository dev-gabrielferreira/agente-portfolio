---
name: conhecimento
description: "Usa e mantém a base de conhecimento — notas dos projetos geradas pelo orquestrador e a memória do Jarvis sobre o Gabriel. Use antes de responder sobre um projeto e quando ele declarar uma preferência ou decisão duradoura."
user-invocable: false
---

# Base de conhecimento

## O que existe

- `/srv/conhecimento/INDEX.md` — índice gerado pelo orquestrador (projetos, o que espera o Gabriel).
- `/srv/conhecimento/projetos/<slug>.md` — nota de cada projeto: o que é, URLs, arquitetura
  (manifesto da stack), decisões (ADRs), features pendentes, armadilhas, últimos jobs. Somente
  leitura; é reescrita quando um job termina.
- `memoria/GABRIEL.md` (nesta pasta) — preferências e decisões que ele declarou. Sua para manter.

## Como buscar (recuperar só o necessário)

1. Nome do projeto conhecido → a nota dele. Desconhecido → `rg -i "<termo>" /srv/conhecimento`.
2. Precisa de detalhe → siga o ponteiro da nota (ADR, arquivo) em `/srv/projetos/<slug>`.
3. Termos exatos (códigos de erro, nomes de função, ids) → `rg` literal; ideias → leia a nota.

## Memória do Gabriel

Quando ele disser algo que vale daqui para frente ("sempre…", "prefiro…", "nunca…", "decidi…"),
anote em `memoria/GABRIEL.md` com a data, em uma linha. Não anote segredos nem o que você deduziu:
só o que ele disse. Se uma linha nova contradiz uma antiga, atualize a antiga.
