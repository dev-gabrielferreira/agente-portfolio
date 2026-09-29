---
name: acompanhar
description: "Responde \"como estão as coisas?\", status de projetos e jobs, e explica falhas do pipeline com opções de ação. Use para qualquer pergunta sobre andamento, fila, erro, projeto fora do ar ou \"o que precisa de mim\"."
user-invocable: false
---

# Acompanhar o pipeline

## Visão geral

`resumo` primeiro. Responda nesta ordem: (1) o que espera o Gabriel, (2) o que está rodando e em
que fase, (3) algo fora do ar. Se nada precisa dele, diga isso em uma linha.

## Fases (para traduzir o `fase` do job)

init → discovery (perguntas) → spec (spec + plano) → design → build (tickets) → gates (lint, tipos,
testes, cobertura, contrato) → tests (testes independentes do test-engineer + mutation) → staging →
evaluate (QA no navegador) → review (segurança + código) → production → publish → retro.

## Quando algo falhou ou parou (`esperando: human`)

1. `job` para ler a mensagem e os últimos eventos; `log_do_job` só se não bastar.
2. Explique a causa em linguagem simples (1–3 linhas) e ofereça opções concretas:
   - `retomar_job` (falha passageira, limite de uso já liberado);
   - `orientar_job` + `retomar_job` (o agente precisa de uma dica que ele pode dar);
   - `pedir_ajustes` (mudar o que foi feito);
   - `cancelar_job` (desistir).
3. Recomende uma opção e espere ele escolher.

## Projeto fora do ar

O monitor abre um job de incidente sozinho. Mostre o que ele encontrou (`job`), a última versão
estável (`projeto` → versão anterior) e lembre que `rollback` existe (exige o código do
autenticador). Não sugira rollback se o incidente já tem correção em staging.
