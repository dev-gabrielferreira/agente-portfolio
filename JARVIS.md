# Jarvis — sua central de comando pelo celular

O Jarvis é uma sessão do **Claude Code rodando 24/7 no seu VPS** com **Remote Control**. Você
conversa com ela pelo app do Claude (aba **Code**), mesmo com o notebook desligado. Ela usa **a sua
assinatura** (nada de API paga) e comanda todo o resto:

- o **pipeline do agente**: criar e adotar projetos, responder as perguntas, aprovar spec, plano e
  design, acompanhar, pedir mudanças em produção e fazer rollback — por conversa ou por **comandos
  com /** (`/status`, `/aprovar`, `/mudar`…);
- **uma conversa por projeto** no app ("Jarvis · <Projeto>"), com as mesmas regras e ferramentas;
- **propostas de mudança no seu Caddyfile principal**, aplicadas só com o seu código;
- **sessões do Claude Code em segundo plano** (`claude --bg`): pesquisas, análises de código e
  protótipos, conversando com elas por mensagens entre sessões;
- a **base de conhecimento** dos projetos: uma nota por projeto, atualizada a cada job;
- os seus **conectores do claude.ai** (GitHub, Drive, Figma…), que ficam disponíveis só para ele.

```
 celular (app Claude → Code)                         VPS
 ─────────────────────────────                        ─────────────────────────────────────────────────
  "Jarvis" ───── Remote Control (HTTPS de saída) ──▶  container jarvis (usuário jarvis, sem Docker)
  "Jarvis · Loja" ┤                                     tmux "jarvis": claude --remote-control Jarvis
  "Jarvis · …"    ┤                                     tmux "projetos": claude remote-control por projeto
  "Laboratório" ──┘                                       (pasta /srv/jarvis/projetos/<slug>, gerada pelo root)
                                                          ├─ CLAUDE.md-índice, comandos /, skills do Jarvis
                                                          │  e do agente (conhecimento), 3 subagentes
                                                          ├─ MCP "agente" ──▶ http://agente:8080/api/v1
                                                          ├─ hooks: situação no início, novidades a cada msg
                                                          └─ sessões em segundo plano → broker (root) ──┐
                                                        usuário lab (sem token, sem acesso ao jarvis):    │
                                                          claude --bg … (sessões do laboratório) ◀────────┘
                                                          tmux "lab": claude remote-control (sessões novas pelo app)
                                                        /srv/projetos (só leitura) · /srv/conhecimento (só leitura)

                                                      container agente (orquestrador + workers claude -p)
                                                        API /api/v1 (token do Jarvis; só rede interna)
                                                        deploy/rollback pela API exigem código TOTP
                                                        Caddyfile principal: proposta → código + confirmação
```

## Por que desse jeito

Decisões tiradas dos vídeos que você mandou e da documentação atual (Claude Code 2.1.284):

| Ideia | Onde está |
|---|---|
| Claude Code 24/7 no VPS com tmux + Remote Control, pela assinatura | container `jarvis` + `supervisor.sh` (reinicia em ~20 s se cair) |
| CLAUDE.md como **índice**, não mega-documento | `jarvis/workspace/CLAUDE.md` (tabela do que existe e onde está) |
| Skills = procedimentos previsíveis; agentes = investigação; subagentes = contexto separado | 7 skills (novo-projeto, acompanhar, aprovacoes, manutencao, sessoes, pesquisa, conhecimento) + `pesquisador` e `analista-de-projeto` |
| Graph engineering: fan-out em paralelo e fan-in | skill `pesquisa` (2–3 subagentes em paralelo, consolidação com fontes) |
| RAG sem exagero: recuperar só o necessário, controle de acesso antes do modelo | base de conhecimento com índice + busca literal (`rg`); projetos montados só leitura; segredos fora do container |
| Economizar limite sem trocar de modelo | tudo no Opus; RTK compacta a saída do Bash, subagentes fazem as leituras grandes e devolvem resumos |
| Sabatina (grill-me) e SDD | o Jarvis conduz as rodadas de perguntas do pipeline pelo celular (skill `novo-projeto`) |
| O que é determinístico não precisa de LLM | aprovação de produção conferida pelo orquestrador (TOTP), não pelo modelo |

## Segurança: o que impede o Jarvis de fazer besteira

| Camada | O que garante |
|---|---|
| Container próprio, sem socket do Docker | o Jarvis não consegue mexer em containers, Caddy ou produção diretamente |
| Laboratório em **outro usuário** (`lab`), criado por um broker root | sessões em segundo plano e as abertas pelo app não herdam o token da API nem leem os arquivos do Jarvis: uma página maliciosa lida numa pesquisa não aprova nada |
| Orquestrador nunca segue link simbólico dos projetos | o que o agente escreve não consegue fazer o root ler segredos para dentro de um prompt nem sobrescrever o banco |
| Projetos e base de conhecimento **montados só leitura** | nada muda em código de projeto fora do pipeline (gates, testes, staging, revisão) |
| API com **token de operador** e **só na rede interna** | o Caddy devolve 404 para `/api/*`, e a API recusa pedidos que passaram pelo proxy |
| **TOTP** para deploy e rollback | o orquestrador confere o código do seu autenticador (uso único, 5 erros bloqueiam por 10 min); prompt injection não gera código |
| Caddyfile principal: **código + confirmação da proposta** | a confirmação chega só no seu celular (push) e no painel, calculada pelo orquestrador sobre aquela proposta: um Jarvis enganado não consegue mostrar uma mudança e aplicar outra. Segredos do arquivo nunca entram no contexto dele (viram «segredo-N»), e as regras barram tirar o `import` dos sites do agente, a proteção da API, expor o `admin` do Caddy ou mover um segredo para onde o visitante veja |
| Código do Jarvis só da imagem | `python -P`/`PYTHONSAFEPATH`: nada que o Jarvis escreva na pasta atual vira código dos hooks ou do MCP; a pasta central e as de projeto são do root (só `memoria/` é dele) |
| Regras `ask` no Claude Code | aprovar, responder perguntas, pedir ajustes, cancelar e rollback abrem confirmação no seu celular |
| Regras `deny` + arquivos do root | o Jarvis não lê credenciais nem edita as próprias regras (`CLAUDE.md`, `.claude/`, `.mcp.json`) |
| Workers sem conectores e com token restrito | quem escreve código não enxerga seu Gmail/Drive nem consegue abrir Remote Control |

O login completo da sua conta fica em `/srv/jarvis/home/.claude` (dentro do container `jarvis`).
Trate o VPS como trata o seu notebook logado.

## Instalação (uma vez, ~5 minutos)

O roteiro completo, do zero ao celular, está em [`PRODUCAO.md`](../PRODUCAO.md). Resumo da parte do
Jarvis — pré-requisito: o agente já instalado e o app do Claude no celular.

```bash
cd ~/agente-portfolio && git pull
sudo ./scripts/setup-jarvis.sh
```

O script faz, na ordem:

1. Gera `JARVIS_API_TOKEN` no `.env`.
2. Gera o segredo TOTP (`ADMIN_TOTP_SECRET`) e mostra o QR code. Escaneie com Google
   Authenticator, Aegis ou 1Password.
3. Sobe os containers `agente` e `jarvis`.
4. Faz `claude auth login` no Jarvis. Escolha a conta Claude (assinatura), abra o link e cole o
   código. Precisa ser o **login completo**: o Remote Control **não funciona** com o token do
   `claude setup-token` (esse token só faz chamadas ao modelo, por isso continua sendo o dos workers
   e das sessões em segundo plano). Opcional: o login do usuário `lab`, para abrir sessões novas
   direto do app no Laboratório.
5. Abre a sessão no terminal (tmux). Lá você:
   - responde `y` em "Enable Remote Control?";
   - em `/config`, liga **Push when actions required** e **Push when Claude decides**;
   - sai com `Ctrl+b` e depois `d` (a sessão continua rodando).

No celular, abra o app do Claude, toque em **Code** e depois em **Jarvis** (ícone de computador com
bolinha verde). Para conferir tudo de uma vez: `sudo ./scripts/verificar-vps.sh` (roteiro completo e
o que cada verificação significa em [`docs/VPS.md`](VPS.md)).

### Tokens: o que é cada um

- **Token da API do Jarvis** (`JARVIS_API_TOKEN`): uma senha aleatória que o Jarvis usa para falar
  com o orquestrador dentro do VPS. **Não é da Anthropic e não gasta nada.**
- **Login do Jarvis** e **token dos workers** (`CLAUDE_CODE_OAUTH_TOKEN`): os dois são da **sua
  assinatura**. Nada é cobrado por uso, desde que `ANTHROPIC_API_KEY` fique vazia (o
  `verificar-vps.sh` avisa se não estiver).

## Usando no dia a dia

Fale normalmente. Exemplos:

- "Como estão as coisas?" → resumo: o que espera você, o que está rodando, o que está fora do ar.
- "Quero um app para registrar leituras dos chillers e ver a eficiência ao longo do mês." → ele
  entende o pedido, abre o job, traz as perguntas do planner em rodadas (cada uma com recomendação)
  e envia suas respostas. Você pode responder algo como "1 ok, 2 só a equipe, 3 pode seguir".
- "Resume a spec e o plano do job 12." → 8 linhas. "Aprova." → a confirmação aparece no celular.
- "O deploy do job 12 está pronto?" → mostra o staging, as mudanças e os vereditos. Aí ele pede o
  código de 6 dígitos do autenticador.
- "No painel de energia, o gráfico mensal está somando errado." → ele lê a nota do projeto, pede a
  um subagente que investigue o código e abre um job de mudança com critérios de aceite.
- "Pesquisa as melhores formas de receber dados BACnet em Python hoje." → pesquisa em paralelo,
  com fontes.
- "Abre uma sessão para prototipar a leitura desse CSV da ONS e me diz se dá para fazer em DuckDB."
  → cria uma sessão em segundo plano no laboratório, acompanha e traz o resultado.

- "Adota o repositório github.com/dev-gabrielferreira/chillers; hoje ele responde em
  chillers.gabrielfdev.com." → abre a adoção: o agente clona, cria testes de caracterização e sobe
  no staging. Antes da produção ele te lembra de tirar o bloco antigo do seu Caddyfile.

Mudar código de projeto **sempre** vira job no pipeline. O Jarvis não edita projeto nem quando é
"só uma linha". É isso que mantém gates, testes independentes e a sua aprovação no caminho.

## Comandos com /

Digite `/` na conversa para ver a lista (`/ajuda` explica cada um). Em conversa de projeto, sem
argumento, o comando vale para o projeto dela.

| Para | Comandos |
|---|---|
| Acompanhar | `/status [projeto]`, `/projeto [slug]`, `/tickets`, `/logs [job]`, `/perguntas` |
| Decidir | `/responder 1 ok, 2 …`, `/plano`, `/aprovar [spec\|design\|deploy] [código]`, `/ajustar <o que mudar>`, `/rollback [código]` |
| Pedir | `/novo <ideia>`, `/mudar <o que>`, `/incidente <o que quebrou>`, `/adotar <repositório>`, `/caddy <mudança>` |
| Controlar | `/pausar`, `/retomar [código]`, `/cancelar` |
| Pesquisar | `/pesquisar <tema>`, `/sessao <tarefa>` |

Os comandos só rodam quando você digita (o modelo não os dispara sozinho). As skills de procedimento
(novo-projeto, aprovações, manutenção, Caddy…) e as **skills do agente** (plano-tecnico,
tickets-verticais, sabatina, testes-que-importam, seguranca-web, api-design…) ficam como
conhecimento: o Jarvis as usa sozinho quando revisa um plano, escreve um pedido de mudança ou
discute testes, com a mesma régua do pipeline.

## Uma conversa por projeto

A conversa **Jarvis** é a central: visão geral, projetos novos, pesquisas. Cada projeto ganha a
conversa **Jarvis · <Projeto>** no app, que aparece cerca de um minuto depois de o projeto existir.
É um servidor Remote Control numa pasta própria (`/srv/jarvis/projetos/<slug>`) que o root gera a
partir da central: mesmo CLAUDE.md (mais a seção que diz de qual projeto é a conversa), mesmos
comandos, skills, subagentes, hooks e MCP, modelo Opus.

| Onde | O que roda | Contexto |
|---|---|---|
| **Jarvis** (central) | a conversa geral com você | contínua (`--continue`); o Claude Code compacta sozinho quando cresce |
| **Jarvis · <Projeto>** | a conversa daquele projeto; hooks e novidades só dele | uma por projeto; dá para abrir até `JARVIS_CONVERSAS_CAPACIDADE` extras no mesmo projeto |
| container **agente** | cada papel do pipeline numa sessão própria, **uma sessão por ticket** do builder | limpo em cada sessão; a memória do projeto fica nos arquivos dele |
| **laboratório** | sessões em segundo plano para pesquisar, analisar ou prototipar | uma por tarefa |
| **base de conhecimento** | uma nota por projeto, atualizada a cada job | é de onde toda conversa tira o estado de um projeto |

Ficam no ar os `JARVIS_CONVERSAS_MAX` (padrão 6) projetos mais recentes mais os
`JARVIS_CONVERSAS_FIXAS`; cada conversa aberta usa ~300–500 MB. O histórico volta sozinho quando o
container reinicia (o servidor retoma as conversas que servia). `JARVIS_CONVERSAS=0` desliga.

## Mudanças no seu Caddyfile principal

`/caddy <o que você quer>` (ou em linguagem natural). O Jarvis lê o arquivo sem os segredos, propõe
edições mínimas e mostra o diff e os alertas; o orquestrador confere as regras e valida no Caddy.
Chega no celular o push "Caddyfile: proposta #N" com o resumo calculado pelo orquestrador e a
**confirmação**. Para aplicar: `/caddy aprovar N <código do autenticador> <confirmação>`, ou o
painel → **Infra** (lá você vê o diff real e aplica só com o código). Na hora: backup em
`/srv/agente/infra/`, `caddy reload` e medição dos sites; se o reload falhar ou um site que
respondia parar, o arquivo anterior volta sozinho. Desfazer: `/caddy desfazer N <código>
<confirmação>` ou o botão no painel. `CADDY_PROPOSALS=false` desliga.

## Laboratório e sessões

- **Laboratório** (`/srv/jarvis/lab`, repositório git): no app, é o ambiente onde você abre sessões
  novas direto do celular. Cada uma trabalha na sua própria worktree. Serve para experimentos seus.
- **Sessões em segundo plano** criadas pelo Jarvis (`nova_sessao`) rodam no mesmo laboratório, como
  usuário `lab`, com permissão `dontAsk`: fazem só o que as regras do laboratório liberam e negam o
  resto, sem travar esperando alguém. O Jarvis acompanha a saída (`saida_da_sessao`), lê as entregas
  em `/srv/jarvis/lab/entregas/` e continua a conversa com `mensagem_para_sessao`. Elas usam o token
  só-modelo do `.env` (o mesmo dos workers); para abrir sessões **pelo app** no Laboratório, o usuário
  `lab` precisa do próprio login completo (passo opcional do setup).
- Todas as sessões usam o **Opus** (o mesmo do Jarvis e do pipeline) e gastam o **mesmo limite da
  assinatura**. Por padrão ficam no máximo 3 ao mesmo tempo. Outro modelo só se você pedir ("abre
  uma sessão com o Sonnet para…").

## Opcional: o que é cada coisa

Nada disto é necessário para o Jarvis funcionar. Ligue só o que resolver algo para você.

| Opção | O que é, em uma frase | Quando vale a pena | Como ligar |
|---|---|---|---|
| **Login do laboratório** | um segundo login da sua assinatura, no usuário `lab`, que faz aparecer o ambiente **Laboratório** no app: você abre conversas novas e livres do Claude Code no VPS, cada uma na sua pasta de trabalho | quando quiser experimentar algo pelo celular sem passar pelo Jarvis (ex.: testar uma biblioteca). As sessões que o **Jarvis** cria no laboratório já funcionam sem isso | responder "s" no `setup-jarvis.sh` |
| **Projects do Claude Code** (beta, "Work locally") | recurso novo do claude.ai/code: você cria um "projeto" no site/app e pede tarefas que rodam numa pasta do VPS | só se o recurso aparecer na sua conta e você preferir organizar por lá; faz quase o mesmo que o Laboratório | precisa do login do laboratório; em claude.ai/code → Projects, tarefa com **Work locally** no ambiente Laboratório (não funciona com "Require trusted devices" ligado) |
| **Eventos em tempo real** (`JARVIS_CHANNEL=1`) | o orquestrador **empurra** avisos para a conversa do Jarvis ("deploy esperando aprovação", "job falhou") e ele te manda push sem você perguntar | se você quer ser avisado pelo próprio Jarvis. Sem isso, ele vê as novidades quando você manda uma mensagem, e o ntfy (`NOTIFY_URL`) continua avisando no celular. É *research preview*: pode mudar | `JARVIS_CHANNEL=1` no `.env` e `docker compose up -d jarvis` |
| **Telegram ou Discord** (`JARVIS_CHANNELS`) | conversar com o Jarvis por um bot do Telegram/Discord em vez do app do Claude | se você vive no Telegram. O app do Claude já cobre tudo | `JARVIS_CHANNELS=plugin:telegram@claude-plugins-official`, `/plugin install telegram@claude-plugins-official` na sessão e parear o bot (documentação de Channels) |
| **Conversas por projeto** (`JARVIS_CONVERSAS`, `_MAX`, `_FIXAS`, `_CAPACIDADE`) | liga/desliga e dimensiona as conversas "Jarvis · <Projeto>" | já vem ligado com 6; diminua se o VPS tiver pouca memória | `.env` e `docker compose up -d jarvis` |
| **Propostas no Caddyfile** (`CADDY_PROPOSALS`) | deixa o Jarvis propor mudanças no seu Caddyfile principal | já vem ligado; aplicar exige sempre código + confirmação | `.env` e `docker compose up -d agente` |
| **Mais rigor no código de 6 dígitos** (`JARVIS_TOTP_FOR`) | a lista de ações que exigem o código do autenticador; o padrão é só `deploy,rollback` | se quiser que aprovar spec/design ou cancelar também peçam o código | `JARVIS_TOTP_FOR=deploy,rollback,spec,design,cancel` |

## Problemas comuns

| Sintoma | Causa provável e correção |
|---|---|
| "Jarvis" não aparece no app | falta o login completo (`docker exec -it -u jarvis jarvis env HOME=/srv/jarvis/home claude auth login`) ou o "Enable Remote Control?" não foi respondido (`docker exec -it -u jarvis jarvis tmux attach -t jarvis`) |
| "Laboratório" não aparece no app | o usuário `lab` não tem login completo (é opcional; rode o `setup-jarvis.sh` de novo e responda "s") |
| `nova_sessao` falha com "broker indisponível" | o supervisor reinicia o broker em ~20 s; veja `docker logs jarvis` |
| `docker logs jarvis` mostra aviso de variável | o container não pode ter `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` nem `DISABLE_GROWTHBOOK`: eles desligam o Remote Control |
| Ferramentas do `agente` falham | `JARVIS_API_TOKEN` diferente nos dois containers (rode `docker compose up -d` depois de editar o `.env`) ou o orquestrador está reiniciando |
| Deploy recusado com o código certo | relógio do celular fora de hora, ou código já usado: espere o próximo. `docker exec agente python -m orchestrator.totp testar` confere |
| "Jarvis · <Projeto>" não aparece | espere 1 minuto; o projeto precisa estar entre os `JARVIS_CONVERSAS_MAX` mais recentes (ou em `_FIXAS`); `docker logs jarvis \| grep conversa`; se a janela pedir "Enable Remote Control?", `docker exec -it -u jarvis jarvis tmux attach -t projetos` |
| Comandos `/` não aparecem | `docker compose up -d jarvis` depois do `git pull` (as regras são copiadas no boot); atualize o app |
| Proposta do Caddy recusada | a mensagem diz a regra ou o erro do Caddy; "obsoleta" = o arquivo mudou depois da proposta, peça outra |
| Sessão some depois de uma queda de rede longa | o servidor Remote Control sai após ~10 min sem rede; o supervisor sobe de novo e a conversa continua (`--continue`) |

## Referências

- Remote Control: https://code.claude.com/docs/en/remote-control
- Sessões em segundo plano (agent view): https://code.claude.com/docs/en/agent-view
- Mensagens entre sessões: https://code.claude.com/docs/en/cross-session-messaging
- Projects: https://code.claude.com/docs/en/claude-projects
- Channels (referência para criar um): https://code.claude.com/docs/en/channels-reference
