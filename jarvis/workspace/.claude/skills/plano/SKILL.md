---
name: plano
description: "Resume spec, plano técnico e tickets para o Gabriel aprovar"
argument-hint: "[projeto ou job]"
disable-model-invocation: true
---
Projeto: o slug em `$ARGUMENTS`, se houver; senão o projeto desta conversa (seção "Esta conversa é do projeto" no CLAUDE.md); na conversa central sem argumento, pergunte qual ou use o único ativo.

1. Ache o job esperando `spec_approval` e leia com `job` (traz `spec_md` e `plano_md`).
2. Leia os tickets em `/srv/projetos/<slug>/.harness/tickets.json` e, se precisar, um ou dois
   arquivos de ticket (são detalhados: objetivo, arquivos, contratos, critérios, fora do escopo).
3. Resuma em até 15 linhas: o que será construído e o que ficou de fora; arquitetura e por quê
   (ADRs); tickets numerados com uma linha cada; riscos; premissas que o planner assumiu.
4. Termine com a sua recomendação e: "`/aprovar spec` ou `/ajustar <o que mudar>`".
Use as skills `plano-tecnico` e `tickets-verticais` como régua para apontar lacunas.
