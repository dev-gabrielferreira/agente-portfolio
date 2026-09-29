---
name: figma-no-pipeline
description: Como usar o Figma MCP dentro do pipeline do agente — criar o arquivo do projeto, levar tokens e telas para o Figma (designer) e ler o design para implementar (builder). Use quando a tarefa mencionar Figma ou quando o Figma estiver habilitado na fase de design.
---

# Figma no pipeline

As skills oficiais da Figma (`figma-use`, `figma-create-new-file`, `figma-generate-library`,
`figma-generate-design`, `figma-design-to-code`) têm as regras de cada ferramenta — **carregue
`figma-use` antes de qualquer `use_figma`**. Esta skill diz *quando* usar cada uma aqui.

## Designer (fase de design)

1. Crie primeiro os entregáveis locais da skill `design-system` (tokens e mockups). Eles são a
   fonte da verdade e funcionam mesmo se o Figma falhar.
2. Arquivo: se `.harness/TASK.md` trouxer `figma_file_key`, use-o; senão crie um com
   `create_new_file` (skill `figma-create-new-file`; o `planKey` vem de `FIGMA_PLAN_KEY` no TASK ou
   do `whoami` quando houver um plano só). Nome: `<nome do projeto> — Design`.
3. Biblioteca: leve `design/tokens.json` para variáveis e crie os componentes do inventário
   (skill `figma-generate-library`).
4. Telas: monte as telas-chave com os componentes (skill `figma-generate-design`), uma seção por
   vez, desktop e mobile.
5. Devolva no relatório a URL do arquivo e o `file_key`. Se qualquer passo do Figma falhar (auth,
   limite), registre o erro no relatório e siga com os mockups locais — não trave o projeto.

## Builder

Com o `file_key` do projeto no TASK, use `get_design_context` e `get_screenshot` do frame da tela
que está implementando (skill `figma-design-to-code`) e adapte ao código e aos tokens do projeto.
Você só **lê** o Figma: as ferramentas de escrita estão bloqueadas para o builder.
