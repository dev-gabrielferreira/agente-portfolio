# Tarefa: avaliar a versão em staging

- **URL do staging:** $staging_url
- **Rodada de avaliação:** $round
- **Foco desta rodada:** $focus

## O que mudou desde a última versão aprovada

```
$changes
```

## Achados da rodada anterior (confirme se foram resolvidos de verdade)

$previous

## Design aprovado

$design

## O que fazer

Siga as instruções do avaliador: percorra os critérios de aceite de `.harness/features.json` no
staging real com o Playwright, teste a API, procure bordas e fachadas, compare com o design e rode
o Lighthouse (Chrome DevTools MCP). Se existir `.harness/evidence/design-lint.txt` (detector de
anti-padrões de design do gate), use-o como pista na nota de design — confirme no navegador antes de
reprovar. Salve screenshots em `.harness/evidence/`. Responda no formato estruturado. Em `features_verified`, liste só os ids que
você verificou de ponta a ponta e que funcionam.

## Skills recomendadas para esta tarefa

$skills
