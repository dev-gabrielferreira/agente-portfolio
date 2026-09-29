---
name: sabatina
description: Técnica de descoberta em rodadas (grilling) para eliminar ambiguidade antes de especificar — árvore de decisões, fronteira, recomendação em cada pergunta, fatos vs decisões. Use na tarefa de descoberta do planner.
---

# Sabatina: perguntas que mudam o que será construído

Baseada no método "grilling" de Matt Pocock (mattpocock/skills, MIT), adaptada para o pipeline:
o Gabriel responde pelo painel ou pelo Jarvis no celular, em rodadas.

## O modelo mental

O pedido é uma **árvore de decisões**. Cada decisão tem pré-requisitos (não adianta perguntar o
formato do relatório antes de saber quem o lê). A **fronteira** é o conjunto de decisões cujos
pré-requisitos já estão resolvidos. Cada rodada pergunta a fronteira inteira — e só ela.

## Fatos ≠ decisões

- **Fato** (você descobre): o que a API pública X oferece, o limite gratuito do serviço Y, o que o
  código atual faz, formato dos dados abertos Z. Pesquise (Context7, web, código). Nunca pergunte.
- **Decisão** (do Gabriel): público, prioridade, o que fica fora, tom/identidade, trade-offs de
  custo, o que tornaria o projeto memorável, dados reais vs simulados.
- **Decisão técnica** (sua, no plano): framework, banco, bibliotecas. Não pergunte — decida no
  plano técnico com ADR. Pergunte só se houver um trade-off de produto por trás ("tempo real ou
  atualização a cada hora?" é produto; "WebSocket ou SSE?" é técnico).

## Formato de cada pergunta

- `question`: uma decisão só, concreta ("Quem usa: você, sua equipe ou o público?").
- `why`: o que muda conforme a resposta ("define login e multiusuário").
- `options`: 2–4 caminhos quando houver.
- `default`: **sua recomendação**, com o motivo embutido quando curto. Ele deve poder responder
  "ok" e seguir.

## Regras das rodadas

1. Rodada 1: a fronteira inicial (tipicamente 4–8 perguntas). Ambiguidade séria do pedido primeiro.
2. Rodadas seguintes: releia as respostas. Resposta vaga ("tanto faz", "o melhor") → você decide,
   registra como premissa e **não** pergunta de novo. Resposta que abre decisões novas → pergunte-as.
3. Fronteira vazia → `questions: []`. Não invente pergunta para preencher rodada.
4. Última rodada: só o que for impossível decidir bem sozinho; o resto vira premissa explícita na
   spec (seção Premissas) — ele revisa na aprovação.

## Anti-padrões

- Perguntar o que está no pedido, o que dá para descobrir, ou "mais alguma coisa?".
- Questionário genérico (prazo, orçamento, público) quando o pedido já responde.
- Perguntas técnicas sem impacto de produto.
- Pergunta dupla ("quer login e notificações?") — separe.
