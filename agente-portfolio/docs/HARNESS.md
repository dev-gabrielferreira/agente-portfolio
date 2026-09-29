# Harness Engineering — o estudo e como ele vira este agente

> **Agente = Modelo + Harness.** O modelo (Claude Opus 5.5) é o motor. O harness é tudo em volta dele:
> as instruções, as ferramentas, as permissões, os testes, os revisores, a memória em arquivos e o
> ciclo de correção. É no harness que você ganha confiabilidade — o modelo você só escolhe.

Este documento resume o que as principais referências de 2025–2026 dizem e mostra, item a item,
onde cada ideia está implementada neste repositório.

---

## 1. De prompt para contexto para harness

| Fase | Disciplina | Pergunta central |
|---|---|---|
| 2022–23 | Prompt engineering | *Como eu falo com o modelo?* |
| 2024–25 | Context engineering | *O que o modelo sabe na hora de agir?* |
| 2026 | **Harness engineering** | *Como o modelo pode agir, e como ele se corrige?* |

O caso mais citado: a LangChain subiu seu agente de código do 30º para o top 5 do Terminal Bench 2.0
**sem trocar de modelo** — só mudando o harness (loops de verificação, mapa de diretórios injetado,
detecção de loop, pensamento concentrado em planejar e verificar). O harness é a principal alavanca
de desempenho que está nas suas mãos.

---

## 2. O modelo mental: guias (feedforward) e sensores (feedback)

Birgitta Böckeler (Thoughtworks, abril/2026) organiza tudo em dois tipos de controle:

- **Guias — feedforward.** Agem *antes* do agente agir e aumentam a chance de ele acertar de primeira.
  Ex.: `CLAUDE.md`, skills, template de projeto, spec, contrato de deploy.
- **Sensores — feedback.** Observam *depois* e permitem que o agente se corrija sozinho.
  Ex.: linter, testes, type checker, revisor de IA, logs, navegador.

Só feedforward = agente que segue regras mas nunca descobre se funcionaram.
Só feedback = agente que repete os mesmos erros. **Precisa dos dois.**

Cada controle também é **computacional** (determinístico, rápido, barato: ruff, pytest, health check)
ou **inferencial** (semântico, caro, não-determinístico: um LLM revisando). Regra prática: use
computacional em *toda* mudança e inferencial nos pontos em que o julgamento vale o custo.

### Onde isso está no agente

| Controle | Direção | Tipo | Onde |
|---|---|---|---|
| Regras do projeto, papéis e fluxo | feedforward | inferencial | `harness/CLAUDE.md` |
| Skills roteadas por papel e stack (próprias + Anthropic, Matt Pocock, Figma, superpowers, Trail of Bits, Vercel, Karpathy, ui-ux-pro-max, Impeccable) | feedforward | inferencial | `harness/catalog.toml`, `orchestrator/skills.py` |
| Sabatina em rodadas: fronteira da árvore de decisões, recomendação em cada pergunta | feedforward | inferencial + humano | skill `sabatina`, fase `discovery` (até `DISCOVERY_ROUNDS`) |
| Plano técnico com ADRs, manifesto da stack e tickets verticais, validado antes da aprovação | feedforward + guarda | inferencial + computacional | fase `plan`, `Pipeline._check_plan_files`, `orchestrator/stack.py` |
| Tickets detalhados (objetivo, contexto, o que construir por camada, arquivos, contratos, 3+ critérios observáveis, costuras, fora do escopo) conferidos antes da aprovação; ticket raso volta ao planner com a lista do que falta (`PLAN_FIX_ATTEMPTS`) | feedforward + guarda | computacional | `orchestrator/tickets.py`, skill `tickets-verticais` (o exemplo dela é testado contra o validador) |
| Um ticket por sessão (contexto limpo, TDD nas costuras do plano), com o ticket inteiro e a definição de pronto no TASK | feedforward | inferencial | fase `build`, skills `tickets-verticais` e `tdd` |
| Skills recomendadas escritas no TASK de cada sessão | feedforward | inferencial | `Library.brief` |
| Documentação atualizada das bibliotecas (Context7 MCP) | feedforward | computacional | `catalog.toml` → `[mcp.context7]` |
| Design aprovado (tokens, mockups, Figma) como contrato visual | feedforward | inferencial + humano | fase `design` |
| Diagnósticos de tipo (Pyright via LSP) a cada edição | feedback | computacional | `harness/plugins/agente-lsp` |
| Propriedade por papel (builder não edita testes do test-engineer etc.) | guarda | computacional | `hooks/protect-files.sh` (aviso na hora, só Write/Edit) |
| Integridade por qualquer caminho: o git compara antes/depois de cada sessão e desfaz o que o papel não podia escrever (inclusive `sed`, `git stash`, arquivo escondido por `.gitignore`) | guarda | computacional | `orchestrator/integrity.py` → `enforce` |
| Gates sempre os do harness; coleta e cobertura com a configuração do harness | guarda | computacional | `integrity.restore_gates`, `check.sh` (`-o addopts=…`, `--cov-config`) |
| Todo teste do test-engineer precisa aparecer executado no junit; config que desativaria testes reprova | feedback | computacional | `integrity.unexecuted_tester_tests`, `integrity.config_problems` |
| Testes independentes escritos a partir da SPEC | feedback | inferencial | subagente `test-engineer`, fase `tests` |
| Detector de testes fracos + rastreio feature → teste | feedback | computacional | `scripts/test_quality.py` |
| Mutation testing (mede se a suíte pega bugs) | feedback | computacional | `scripts/mutation.sh` |
| Fuzz de contrato a partir do OpenAPI | feedback | computacional | `tests/test_api_contract.py` (Schemathesis) |
| E2E + acessibilidade (axe) e Lighthouse | feedback | computacional + inferencial | `tests/e2e/`, Chrome DevTools MCP no avaliador |
| Revisão de código com revisores oficiais especializados | feedback | inferencial | `code-reviewer` + plugin `pr-review-toolkit` |
| Lições aprendidas (cresce com o uso) | feedforward | inferencial | `harness/lessons/LESSONS.md` → `.claude/rules/lessons.md` |
| Andaime do harness + contrato de deploy + starters opcionais | feedforward | computacional | `harness/project-template/`, `harness/starters/` |
| Spec e plano aprovados por você | feedforward | inferencial + humano | fases `spec` e `plan` |
| Gate do frontend (deps, lint, tipos, testes, build; `.only/.skip` proibidos) e comandos "que sempre passam" reprovados | feedback + guarda | computacional | `check.sh` (seção frontend), `integrity.config_problems` |
| Detector de anti-padrões de design (61 regras, sem LLM) | feedback | computacional | `impeccable detect` no `check.sh` → `.harness/evidence/design-lint.txt` |
| Bloqueio de comandos perigosos | guarda | computacional | `hooks/guard.sh` |
| Proteção dos arquivos do harness | guarda | computacional | `hooks/protect-files.sh` |
| Lint + format a cada edição, com mensagem de correção | feedback | computacional | `hooks/post-edit-check.sh` |
| Não deixa encerrar com checagem vermelha | feedback | computacional | `hooks/stop-gate.sh` |
| `scripts/check.sh` (ruff, Pyright, testes fracos, pytest, fuzz, e2e, cobertura, pip-audit) | feedback | computacional | gate do orquestrador |
| Build da imagem + health check em staging | feedback | computacional | `orchestrator/deployer.py` |
| Avaliador em contexto limpo com Playwright | feedback | inferencial | `.claude/agents/evaluator.md` |
| Revisor de segurança | feedback | inferencial | `.claude/agents/security-reviewer.md` |
| Sua aprovação antes de produção | feedback | humano | fase `awaiting_approval` |
| Monitor de saúde em produção → incidente | feedback contínuo | computacional | `orchestrator/monitor.py` |
| Retrospectiva que propõe lições | steering loop | inferencial + humano | fase `retro` |

---

## 3. A arquitetura da Anthropic para apps longos: planner → generator → evaluator

Dois artigos de engenharia da Anthropic (nov/2025 e mar/2026) são a referência de design:

1. **Planner.** Expande um pedido curto numa spec ambiciosa, focada em *produto* e desenho técnico
   de alto nível — não em detalhes de implementação, porque um erro granular na spec contamina todo o
   resto. Sem planner, o gerador "sub-escopa" e entrega menos.
2. **Generator (builder).** Implementa uma feature por vez, commita, se auto-avalia antes de entregar.
3. **Evaluator.** Um agente *separado* que clica pelo app rodando (Playwright), testa API e estado do
   banco, e dá nota contra critérios com **limiar mínimo** — qualquer critério abaixo reprova.

Achados que moldaram este agente:

- **Autoavaliação não funciona.** Agentes elogiam o próprio trabalho mesmo quando é medíocre.
  Separar quem faz de quem julga é a alavanca mais forte, e é bem mais fácil calibrar um avaliador
  cético do que tornar o gerador autocrítico.
- **O avaliador vem "bonzinho" de fábrica.** Ele acha o bug e se convence de que não é grave. A
  calibração é iterativa: ler os logs do avaliador, achar onde o julgamento dele diverge do seu,
  ajustar o prompt. (Isso é o ciclo de melhoria descrito na seção 7.)
- **Critérios escritos mudam o resultado antes mesmo do feedback.** Penalizar explicitamente
  "cara de template/IA" empurra o design para algo com identidade.
- **O avaliador vale o custo quando a tarefa está na borda do que o modelo faz sozinho.** Com Opus
  4.6+, sprints por feature viraram overhead; planner e avaliador continuaram pagando o custo.
- **Comunicação por arquivos.** Os agentes conversam escrevendo e lendo arquivos — auditável e
  sobrevive a reinícios.
- **Custo de referência:** solo 20 min / US$ 9 com o app central quebrado; harness completo
  ~4 h / US$ 125 com o app funcionando. No seu caso o custo é absorvido pela assinatura, mas o
  **limite de uso** passa a ser o recurso escasso — por isso o orquestrador roda um job por vez.

### Onde isso está no agente

- Planner → `.claude/agents/planner.md`, usado nas fases `discovery` (rodadas), `spec` (o quê) e
  `plan` (como: arquitetura, ADRs, manifesto, tickets) — o Spec-Driven Development (github/spec-kit)
  e o fluxo grill → spec → tickets → implement (Matt Pocock) adaptados para rodar sem terminal.
- Generator → uma sessão `claude -p` **por ticket**, guiada por `CLAUDE.md` + `.harness/TASK.md`
  (o ticket vem embutido): o "half loop" dos vídeos — passos pequenos, contexto limpo em cada um.
- Evaluator → `.claude/agents/evaluator.md`, sem ferramentas de escrita, com Playwright MCP apontado
  para o **staging real**, retornando veredito estruturado (`--json-schema`) com notas e limiares.
- Rodadas limitadas (`MAX_FIX_ROUNDS`): passou do limite, o agente para e te chama.

---

## 4. Os três primitivos do loop de qualidade

Do repositório `anthropics/cwc-long-running-agents` (Code with Claude 2026):

1. **Contrato default-FAIL.** Cada feature nasce `"passes": false` em `.harness/features.json`.
   O agente só pode marcar `true` depois de **abrir uma evidência** (screenshot, relatório de teste).
   Um hook bloqueia a escrita se não houver leitura de evidência.
   → `hooks/track-read.sh` + `hooks/verify-gate.sh`.
   E mais: para o orquestrador, `passes: true` é só uma *alegação* — quem decide é o avaliador.
2. **Avaliador em contexto limpo.** Nunca viu o build, não pode editar, só julga.
3. **Handoff mantido pelo agente.** `.harness/PROGRESS.md` é o diário; git é a segunda memória;
   `hooks/commit-on-stop.sh` é a rede de segurança que commita o que sobrou.

Mais dois controles de operador: **kill switch** (arquivo `AGENT_STOP` trava toda ferramenta) e
**steer** (arquivo `STEER.md` injeta uma instrução sua no meio da execução). Os dois têm botão no painel.

---

## 4b. Quem testa não é quem constrói

O mesmo princípio que separa gerador e avaliador vale para os testes. Se o builder escreve os testes
que decidem se ele terminou, ele tende a escrever testes que passam — não testes que pegam bugs. Por
isso:

1. O **test-engineer** escreve a suíte de aceitação a partir da **SPEC**, em outra sessão, sem
   compromisso com o código.
2. Um hook impede o builder de editar esses testes. Discordância vira **disputa** registrada e
   julgada pelo test-engineer — nunca um teste afrouxado em silêncio.
3. A suíte é **medida**: mutation testing (o teste precisa falhar quando o código é alterado de
   propósito), fuzz de contrato (entradas que ninguém pensou), detector de testes fracos e rastreio de
   cada feature até um teste. Cobertura sozinha não diz nada sobre isso.

Numa execução real (Claude Code 2.1.281) sobre um projeto com um bug plantado, o test-engineer
escreveu 13 testes de aceitação, 7 propriedades e um e2e com axe, e encontrou o bug (500 em vez de
422). O builder, na sessão seguinte, reproduziu o bug com um teste de unidade antes de corrigir e não
tocou nos testes de aceitação.

## 5. Contexto: o agente é amnésico, o sistema de arquivos não

- Janela de contexto enche → o modelo perde coerência. Soluções: **compactação** (resumo no lugar)
  ou **reset** (sessão nova + handoff). Modelos recentes (Opus 4.5+) praticamente não sofrem de
  "ansiedade de contexto", então aqui cada **fase** é uma sessão nova com handoff em arquivo
  (SPEC.md, features.json, PROGRESS.md, NEXT_FINDINGS.md) e, dentro da fase, a compactação
  automática do Claude Code cuida do resto.
- O prompt de cada sessão é curto: *"sua tarefa está em `.harness/TASK.md`"*. O orquestrador escreve
  a tarefa em arquivo — sem limite de tamanho de argumento e com registro do que foi pedido.

---

## 5b. Contexto sob medida: skills e MCPs por papel

Mais contexto não é melhor contexto. Cada papel recebe só as skills e ferramentas da sua etapa, e só as
da stack do projeto (tags definidas pelo planner). As fontes externas ficam fixadas por commit, para
que uma mudança upstream nunca altere o comportamento do agente sem você saber. Os servidores MCP que
um papel não deve usar são bloqueados por servidor (`--disallowedTools mcp__<servidor>`), e os
conectores do claude.ai ficam desligados no usuário do agente.

## 6. Harnessability sem template engessado: o manifesto

Pela **Lei de Ashby**, um regulador só controla o que ele consegue modelar. Na primeira versão isso
virou "todo projeto nasce do mesmo template FastAPI" — seguro, mas os projetos saíam parecidos, e
arquitetura de portfólio precisa mostrar *decisão*. A troca foi manter a variedade que os sensores
conseguem modelar **declarada**, em vez de fixa:

- O que é fixo é o **contrato da plataforma** (container único, porta 8000, `/health`, `/data`,
  config por ambiente) e a **linguagem dos testes** (pytest; backend Python quando existir).
- O resto o planner escolhe e justifica em ADR, e declara no manifesto `.harness/stack.json`:
  como subir o app (`start`, `health`), onde está a aplicação ASGI e a configuração, qual pasta
  é regra de negócio (alvo do mutation testing), onde está o frontend e como checá-lo, onde está a
  interface (detector de design).
- Os sensores leem o manifesto: fixtures de teste, e2e, fuzz de contrato, cobertura, mutation, gate
  do frontend, detector de design. O orquestrador valida o manifesto antes da aprovação e reprova
  comandos que "sempre passam".
- **Starters** (`python-fastapi`, `fastapi-react`) continuam existindo como atalho — copiados só se o
  plano escolher, só arquivos que faltam, antes do primeiro ticket.

---

## 7. O steering loop: como o agente melhora com o tempo

> *"Sempre que um problema acontece mais de uma vez, os controles de feedforward e feedback
> devem ser melhorados para torná-lo menos provável — ou impossível."* — Böckeler

O agente não se auto-modifica sem você. O ciclo é:

1. Todo job registra: falhas de gate, achados do avaliador, rodadas, custo, tempo, rollbacks.
2. Ao fim, a fase `retro` lê esse histórico e **propõe lições** classificadas como
   `rule` (vira texto em LESSONS.md), `skill` (mudar uma skill), `check` (novo teste/lint) ou `hook`.
3. Você aprova ou descarta no painel. Aprovadas entram em `harness/lessons/LESSONS.md` e passam a ser
   carregadas por todo projeto no próximo job.
4. Lições que viram `check` ou `hook` são as mais valiosas: transformam um conselho inferencial num
   sensor computacional (não depende mais do modelo lembrar).

E o inverso — **simplificar**: a cada novo modelo, desligue um componente por vez e veja se ele ainda
é "load-bearing". Todo pedaço de harness codifica uma suposição sobre o que o modelo *não* consegue
fazer sozinho, e essas suposições envelhecem. Veja `docs/MELHORIA.md`.

---

## 8. O que nenhum sensor pega (e por isso você fica no loop)

Nem sensores computacionais nem inferenciais pegam com confiabilidade: **diagnóstico errado,
over-engineering, feature desnecessária e instrução mal entendida**. Correção funcional depende de
você ter especificado o que queria. Por isso os dois pontos humanos deste agente estão onde o seu
julgamento rende mais: **aprovar a spec** (barato, evita construir a coisa errada) e **aprovar o
deploy em produção** (protege o que está no ar). O resto é automático.

---

## 9. Uma central para o humano no loop: o Jarvis

O harness decide *quando* o humano entra (respostas, spec + plano, design, deploy). O Jarvis
([`JARVIS.md`](JARVIS.md)) decide *por onde*: uma sessão do Claude Code 24/7 no VPS, com Remote
Control, que você usa pelo celular. Ela é um agente coordenador — conversa, resume, pesquisa com
subagentes em paralelo, cria sessões em segundo plano — mas não é um atalho em volta dos sensores:
mudança de código sempre vira job no pipeline, e o que é irreversível (deploy, rollback) exige um
código TOTP conferido pelo orquestrador. É o mesmo princípio do resto do harness: o que pode ser
verificado deterministicamente não fica a cargo do modelo.

## Fontes

- Anthropic — *Effective harnesses for long-running agents* (nov/2025)
- Anthropic — *Harness design for long-running application development* (mar/2026)
  https://www.anthropic.com/engineering/harness-design-long-running-apps
- Anthropic — `cwc-long-running-agents` (primitivos de harness) https://github.com/anthropics/cwc-long-running-agents
- Birgitta Böckeler — *Harness engineering for coding agent users* (abr/2026)
- github/spec-kit — Spec-Driven Development (templates de spec, plano, tarefas e constituição)
- mattpocock/skills — grilling, to-spec, to-tickets, tdd, domain-modeling, codebase-design
- Attekita Dev (YouTube) — Claude Code 24/7 no VPS, harness engineering, RAG, skills do Matt Pocock,
  browser harness, graph engineering, economia de tokens (RTK), agentes vs skills vs subagentes, SDD
- Documentação do Claude Code — Remote Control, agent view (`claude --bg`), cross-session messaging,
  Projects, Channels
  https://martinfowler.com/articles/harness-engineering.html
- LangChain — *Improving Deep Agents with Harness Engineering*
- Lista curada: https://github.com/ai-boost/awesome-harness-engineering
- Docs do Claude Code: headless https://code.claude.com/docs/en/headless ·
  hooks https://code.claude.com/docs/en/agent-sdk/hooks · CLI https://code.claude.com/docs/en/cli-reference
