# Tarefa: retrospectiva do job (melhoria do harness)

Você está ajudando a melhorar o **harness** — as regras, skills, testes e hooks que guiam o agente
de desenvolvimento — a partir do que aconteceu neste job. Não altere nenhum arquivo.

## Relatório do job

$report

## Sugestões que o próprio agente deixou em .harness/HARNESS_FEEDBACK.md

$feedback

## Lições que já existem (não repita)

$lessons

## O que fazer

Proponha no máximo 5 lições, só as que teriam evitado retrabalho real visto no relatório
(falha de gate repetida, achado do avaliador, rollback, bloqueio). Para cada uma:

- `text`: regra curta e acionável no imperativo, geral o suficiente para valer em outros projetos;
- `kind`: `check` ou `hook` quando dá para transformar em verificação automática (melhor opção:
  não depende do modelo lembrar), `skill` quando é conhecimento de como fazer, `rule` nos demais;
- `evidence`: o trecho do relatório que motiva a lição.

Se nada justificar uma lição, devolva a lista vazia — lição fraca polui o contexto de todos os
projetos. Em `simplifications`, aponte partes do harness que pareceram não agregar neste job.
