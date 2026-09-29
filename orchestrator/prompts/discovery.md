# Tarefa: descoberta — rodada $round de no máximo $max_rounds (planner, Tarefa A)

Tipo de trabalho: **$job_type**

## Pedido do Gabriel

$request

## Contexto do projeto

$context

## Rodadas anteriores (perguntas e respostas do Gabriel)

$history

## O que fazer

Siga a skill `sabatina`. Pense no pedido como uma **árvore de decisões** e pergunte agora a
**fronteira**: todas as decisões cujos pré-requisitos já estão resolvidos — nenhuma a mais.

- Fatos você descobre sozinho (lendo o código, SPEC.md, documentação via Context7, a web);
  decisões de produto, prioridade e gosto são do Gabriel.
- Cada pergunta traz `why` (o que muda conforme a resposta), `options` quando fizer sentido e um
  `default` que é a sua **recomendação** (ele pode responder só "ok").
- Rodada 2 em diante: pergunte só o que as respostas anteriores abriram ou deixaram vago. Não repita
  pergunta respondida. Se a fronteira está vazia, devolva `questions: []` — a spec começa.
- $last_round_rule
- Se o projeto já existe, leia SPEC.md, docs/PLAN.md, `.harness/stack.json` e o código relevante antes.

Responda no formato estruturado: `questions`, `project_name` e `one_liner` (projeto novo; nome curto
e memorável, sem "AI"/"GPT" genérico) e `understanding` (3–5 frases do que será construído,
incorporando as respostas). Cada campo é um campo próprio do objeto — não escreva tags nem outros
campos dentro dos textos.

Não crie nem altere arquivos nesta tarefa.
