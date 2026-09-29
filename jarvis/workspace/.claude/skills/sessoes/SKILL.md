---
name: sessoes
description: "Cria e usa sessões do Claude Code em segundo plano (pela assinatura) para pesquisas, análises, protótipos e tarefas longas, e conversa com elas. Use quando uma tarefa for grande, paralelizável ou não precisar acontecer nesta conversa."
user-invocable: false
---

# Sessões em segundo plano

Cada sessão é um Claude Code completo, com contexto próprio, rodando no laboratório
(`/srv/jarvis/lab`, cada uma na sua worktree). Elas gastam o mesmo limite da assinatura.

## Quando usar

- pesquisa longa ou comparação de ferramentas; análise profunda do código de um projeto
  (`projeto` anexa o código em modo leitura); protótipo descartável; relatório.
- **Não** use para mudar projeto do portfólio — isso é job do pipeline (skill `manutencao`).
- Perguntas rápidas: faça você mesmo ou use um subagente (skill `pesquisa`).

## Como criar (`nova_sessao`)

- `nome`: curto e descritivo (`comparar-filas`, `auditoria-links`).
- `tarefa`: autossuficiente — objetivo, contexto, limites, **formato da entrega** e onde salvar
  (ex.: "salve em entregas/comparar-filas.md e termine com um resumo de 5 linhas").
- `agente`: `pesquisador`, `analista-de-projeto` ou `prototipador` quando couber.
- `modelo`: omita. O padrão é o Opus, o mesmo do Jarvis e do pipeline. Só informe outro
  (ex.: `sonnet`) quando o Gabriel pedir explicitamente para economizar limite.

## Acompanhar e conversar

- `sessoes` lista (id, nome, estado). `saida_da_sessao` mostra o que ela produziu; as entregas
  ficam em `/srv/jarvis/lab/entregas/` (você pode ler).
- Para responder uma pergunta da sessão ou mandar a próxima etapa, use `mensagem_para_sessao` com o
  id completo — de preferência quando ela terminou o turno (numa sessão ainda rodando, o Claude Code
  cria uma cópia em vez de interromper).
- As sessões do laboratório rodam como **outro usuário**, sem acesso ao MCP `agente` nem aos seus
  arquivos: é de propósito (uma página maliciosa lida por uma pesquisa não consegue aprovar nada).
  Por isso elas também não recebem mensagens por SendMessage — use a ferramenta acima.
- Sessões rodam sem ninguém para aprovar: o que não estiver liberado nas permissões do laboratório
  é negado. Se ela travar por isso, conte ao Gabriel o que ela queria fazer.
- Terminou? Resuma para ele e `parar_sessao`. No máximo 3 ao mesmo tempo.
