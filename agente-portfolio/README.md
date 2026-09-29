# Agente de Portfólio

Um agente de desenvolvimento que recebe um pedido seu, te sabatina em rodadas até não sobrar
ambiguidade, escreve a spec e um plano técnico com arquitetura escolhida para *aquele* produto (sem
template engessado), desenha a interface, constrói um ticket por vez, é testado por um engenheiro de
testes independente, corrige os próprios erros, publica em staging, passa por um avaliador e por
revisões de segurança e de código, espera o seu OK e coloca em produção no seu VPS — depois monitora,
mantém e aprende com cada job. E você comanda tudo **pelo celular**, pelo Jarvis.

- **Motor:** Claude Code com **Claude Opus 5.5** (`claude-opus-5-5`) usando a sua assinatura.
- **Jarvis:** sessão do Claude Code 24/7 no VPS com Remote Control — a central que você usa pelo app
  do Claude, mesmo com o notebook desligado ([`docs/JARVIS.md`](docs/JARVIS.md)).
- **Harness:** regras, hooks, 7 subagentes, ~45 skills (próprias + Anthropic, Matt Pocock, Figma,
  superpowers, Trail of Bits, Vercel, ui-ux-pro-max, Impeccable), MCPs por papel e por stack, e
  sensores que leem o manifesto da arquitetura de cada projeto
  (a teoria está em [`docs/HARNESS.md`](docs/HARNESS.md)).
- **Orquestrador + painel:** FastAPI + SQLite em `agent.gabrielfdev.com`; API interna para o Jarvis.
- **Deploy:** Docker + Caddy no seu VPS, com staging, aprovação, health check e rollback automático.

```
 você ─pedido─▶ perguntas ⇄ respostas ─▶ spec ─▶ plano ─aprova─▶ design ─aprova─▶ build ⇄ gates ⇄ test-engineer
 (painel ou     (planner, em rodadas)     (o quê) (como:   (spec+    (designer:  (1 ticket  (lint, tipos,  (aceitação,
  Jarvis no                                        ADRs,    plano)    3 direções, por sessão) testes, fuzz,  e2e, a11y,
  celular)                                         tickets)           Figma)                 frontend,      propriedades,
                                                                                             design-lint)   mutation)
                                                                 │ tudo verde
                                        staging ─▶ avaliador ─▶ revisão (segurança + código)
                                                   (Playwright,   (security-reviewer, code-reviewer
                                                    Lighthouse)    + revisores oficiais)
                                                                 │ passou             ▲ reprovou → build
              retrospectiva ◀── GitHub ◀── produção (rollback se falhar) ◀──aprova── você
                    │
               lições → harness de todos os projetos
```

---

## 1. Como ele funciona

### O fluxo de um projeto novo

| Fase | Quem | O que acontece | Se falhar |
|---|---|---|---|
| **perguntas** (em rodadas) | planner (Opus) | Sabatina: modela o pedido como árvore de decisões e pergunta a fronteira — só o que muda o que será construído, cada pergunta com a recomendação dele. Rodadas seguintes só aprofundam o que suas respostas abriram (até `DISCOVERY_ROUNDS`, 3) | — |
| **spec** | planner | Escreve `SPEC.md` (requisitos FR-/SC- numerados, premissas), o glossário `CONTEXT.md` e `.harness/features.json` (todas as features começam `passes: false`) | valida estrutura; se inválida, te chama |
| **plano técnico** | planner | Escolhe a arquitetura para este produto (sem stack padrão) e registra: `docs/PLAN.md` (componentes, dados, contratos, costuras de teste, alternativas pesquisadas), ADRs em `docs/adr/`, o manifesto `.harness/stack.json` que os gates leem, um starter opcional e os **tickets em fatias verticais, detalhados** (objetivo, contexto, o que construir por camada, arquivos, contratos, critérios de aceite, costuras de teste, fora do escopo, riscos) | valida manifesto, dependências, cobertura de features e **a forma de cada ticket**; se reprovar, devolve a lista ao planner para corrigir (1 vez) e só então te chama |
| ⏸ **você aprova spec + plano** | você | Lê resumo, features, arquitetura, ADRs, tickets, variáveis e riscos (no painel ou resumido pelo Jarvis). Aprova ou pede ajustes | — |
| **design** | designer (Opus) | Explora 3 direções visuais (base offline de estilos/paletas/fontes do ui-ux-pro-max, anti-padrões do Impeccable), desenvolve a melhor: identidade, tokens, componentes e 3–5 mockups (desktop e mobile); no Figma também, se habilitado | — |
| ⏸ **você aprova o design** | você | Vê os screenshots, abre os mockups e o arquivo do Figma. Aprova ou pede ajustes | — |
| **build** | builder (Opus) | **Um ticket por sessão**, com contexto limpo e o ticket inteiro no briefing: o primeiro monta a fundação (starter escolhido ou do zero, conforme o plano); os seguintes são fatias verticais com TDD nas costuras definidas no plano. Commits, diário em `PROGRESS.md`, Pyright a cada edição | ticket que não fecha em 2 sessões é marcado bloqueado e os gates dizem o que falta |
| **gates** | orquestrador | `scripts/check.sh`, guiado pelo manifesto: ruff, Pyright, detector de testes fracos, pytest (unidade, aceitação, propriedades), fuzz de contrato, e2e, cobertura ≥70%, pip-audit; **frontend** (dependências, lint, tipos, testes, build, `.only/.skip` proibidos); **detector de anti-padrões de design** (Impeccable, 61 regras sem LLM); contrato de deploy, features pendentes, segredos | volta ao build com a saída do erro |
| **testes** | test-engineer (Opus, independente) | Escreve os testes de aceitação a partir da **spec**, não do código; e2e com Playwright + axe; propriedades (Hypothesis); investiga o fuzz; mede a suíte com **mutation testing**; julga disputas do builder | bug real → builder corrige o código (não pode tocar nos testes); teste fraco, feature sem teste ou mutation < 60% → volta ao test-engineer |
| **staging** | orquestrador | `docker build`, sobe `<projeto>-staging.gabrielfdev.com`, espera o HEALTHCHECK e o `/health` público | volta ao build com o log do container |
| **avaliação** | evaluator (Opus, contexto limpo) | Usa o app de verdade com Playwright, compara com o design, roda Lighthouse (Chrome DevTools MCP), dá nota com limiar mínimo em funcionalidade, profundidade, design, código e acessibilidade | volta ao build com os achados |
| **revisão** | security-reviewer + code-reviewer | Segurança (diff, semgrep, revisão diferencial) e manutenção (falhas silenciosas, lacunas de teste, over-engineering, fraude de teste) usando os revisores oficiais do pr-review-toolkit | bloqueante volta ao build |
| ⏸ **você aprova o deploy** | você | Abre o staging, vê notas, achados e commits. Aprova ou pede ajustes | — |
| **produção** | orquestrador | Promove **a mesma imagem** do staging; health check | **rollback automático** para a versão anterior |
| **publicação** | orquestrador | Cria/atualiza o repositório público no GitHub e marca a tag do deploy | avisa, não bloqueia |
| **retrospectiva** | Opus | Lê o histórico do job e propõe lições para o harness | — |

Passou de `MAX_FIX_ROUNDS` (4) rodadas de correção ou `MAX_TEST_ROUNDS` (3) rodadas de teste, ele
para e te pede orientação.

**Modelo:** todos os papéis (perguntas, spec, plano, design, build, testes, avaliação, revisão e
retrospectiva), o Jarvis e as sessões do laboratório usam o **Opus** (`AGENT_MODEL` e `JARVIS_MODEL`
= `claude-opus-5-5`), pela assinatura.

### Os testes "que realmente importam"

O builder não escreve os testes que decidem se ele terminou. Quem escreve é o **test-engineer**, em
outra sessão, a partir da SPEC; o builder não consegue alterar esses arquivos (hook) e, se discordar,
abre uma disputa que o test-engineer julga. E a qualidade da suíte é **medida**, não presumida:

| Sensor | O que pega | Exemplo real (medido na construção deste agente) |
|---|---|---|
| Mutation testing (mutmut) | teste que passa com código errado | teste "roda sem erro": 9% dos mutantes mortos; tabela de fronteiras: 91% |
| Fuzz de contrato (Schemathesis) | 500 e respostas fora do OpenAPI | achou `total=0 → 500` sem ninguém escrever o caso |
| Detector de testes fracos | `assert x is not None`, só status 200, `assert True`, skip escondido, `sleep`, exceção engolida, sem assert | reprova o gate e diz como corrigir |
| Rastreio feature → teste | feature sem teste de aceitação | cada id de `features.json` precisa de `@pytest.mark.feature("F0X")` |
| axe + Lighthouse | acessibilidade e qualidade da página | contraste insuficiente reprovado no e2e |
| Pyright | erro de tipo | diagnóstico a cada edição + gate |

Numa execução real do test-engineer sobre um projeto com um bug plantado, ele escreveu 13 testes de
aceitação, 7 propriedades e um e2e com axe, encontrou o bug (500 em vez de 422) e percebeu que o
mutation testing precisava esperar a correção — tudo sozinho, em 45 turnos.

### Os outros tipos de job

- **Mudança** num projeto no ar: perguntas (só se necessário) → a spec ganha uma seção nova → o plano ganha os tickets da mudança (e ADRs, se a arquitetura mudar) → mesmo fluxo, sem a aprovação de spec.
- **Incidente:** você relata, ou o monitor detecta 3 falhas seguidas no `/health`. O agente recebe os logs, reproduz com um teste, corrige, e a correção espera a sua aprovação antes da produção.
- **Adoção** de um projeto que já existe (seus dois projetos atuais): clona do GitHub, mapeia, adapta ao contrato de deploy **sem mudar comportamento**, cria testes de proteção — e só troca a versão em produção depois do seu OK.

### Por que é seguro deixar ele mexer em produção

| Risco | Controle |
|---|---|
| Agente rodando comando destrutivo | Roda como usuário `agent` sem sudo e **sem acesso ao Docker**; hook `guard.sh` bloqueia `docker`, `sudo`, `git push`, `rm -rf /`, leitura de `.env`, `curl | sh`… |
| Agente enfraquecendo os próprios testes | Os testes que decidem são do test-engineer e o builder não pode editá-los — nem pelo shell: depois de cada sessão o orquestrador compara o git e desfaz o que o papel não podia escrever. Gates restaurados do harness antes de rodar; `addopts`, `pytest.ini` ou conftest que desmarque testes reprovam; todo teste do test-engineer precisa aparecer executado no junit; o code-reviewer ainda procura fraude de teste |
| Agente acessando suas contas | Conectores do claude.ai desligados no usuário `agent`; cada papel só enxerga os MCPs dele (os outros são bloqueados por servidor); `.mcp.json` do projeto é bloqueado |
| Agente dizendo que terminou sem terminar | Contrato default-FAIL com evidência obrigatória + avaliador independente + gates do orquestrador |
| Vazamento de segredo | Segredos ficam em `/srv/agente/secrets` (só root); o ambiente do agente é montado do zero (sem token do GitHub, sem senha do painel); varredura de segredos no gate |
| Deploy quebrado | Staging primeiro, mesma imagem em produção, health check duplo, rollback automático |
| Mexer no seu Caddyfile principal | O Jarvis só propõe (sem ver os segredos do arquivo); aplicar exige o código do autenticador **e** a confirmação daquela proposta, que só chega no seu celular; regras barram tirar o `import` dos sites do agente, a proteção da API e expor o `admin`; backup, validação no Caddy e volta automática se um site parar |
| Hook malicioso no repositório | O git do orquestrador roda com hooks desligados e o push sai de um clone limpo |
| Rede interna do VPS | O container do agente só enxerga o Caddy (rede `agente-painel`), não a `interna` |
| Gasto descontrolado | Limites de turnos, tempo por sessão, rodadas de correção, um job por vez; limite de uso da assinatura reagenda o job sozinho |

---

## 2. Instalação no VPS

> **Roteiro completo, do zero ao Jarvis no celular, com todos os comandos: [`PRODUCAO.md`](PRODUCAO.md).**
> Abaixo, o resumo de cada passo.

Pré-requisitos (você já tem): Ubuntu 24.04, Docker, Caddy em container, rede Docker `interna`,
domínio `gabrielfdev.com`. Recomendo **pelo menos 4 GB de RAM livres** (Chromium do avaliador +
builds). Se o VPS for pequeno, suba o swap para 4 GB.

### Passo 1 — DNS
No painel do seu DNS, crie:
- `A  agent.gabrielfdev.com  → IP do VPS`
- `A  *.gabrielfdev.com      → IP do VPS` (curinga: cada projeto ganha `<nome>.` e `<nome>-staging.`)

### Passo 2 — Token do Claude (assinatura)
No **seu computador** (precisa de navegador), com o Claude Code instalado:
```bash
npm install -g @anthropic-ai/claude-code
claude setup-token
```
Faça login com a conta da assinatura e copie o token gerado (`sk-ant-oat…`). Ele vai no `.env`.

> Use o plano **Max**: um projeto completo com Opus consome muitas horas de uso. Quando o limite é
> atingido, o job é reagendado sozinho (`RATE_LIMIT_BACKOFF_S`). Não defina `ANTHROPIC_API_KEY`
> junto — se ela existir, tem prioridade e é cobrada por uso. Esse token é para **uso pessoal**
> seu; não ofereça o agente a terceiros com a sua assinatura.

### Passo 3 — Token do GitHub
GitHub → Settings → Developer settings → **Fine-grained tokens** → acesso a "All repositories" da
conta `dev-gabrielferreira`, permissões **Contents: Read and write** e **Administration: Read and
write** (para criar repositórios). Validade de 1 ano; anote para renovar.

### Passo 4 — Clonar e instalar
Suba esta pasta para um repositório **privado** seu no GitHub (ex.: `agente-portfolio`) e, no VPS:
```bash
sudo git clone https://github.com/dev-gabrielferreira/agente-portfolio /opt/agente-portfolio
cd /opt/agente-portfolio
sudo cp .env.example .env && sudo chmod 600 .env
sudo nano .env        # CLAUDE_CODE_OAUTH_TOKEN, GITHUB_TOKEN, GIT_AUTHOR_EMAIL, NOTIFY_URL…
sudo ./scripts/install-vps.sh
```
O script cria `/srv/agente`, a rede `agente-painel`, gera senha do painel e `SESSION_SECRET` se
estiverem vazios, faz o build, sobe o container e **verifica que o usuário `agent` não consegue usar
o Docker** (se conseguir, ele aborta).

### Passo 5 — Caddy
No seu `Caddyfile`, acrescente o conteúdo de [`deploy/Caddyfile.snippet`](deploy/Caddyfile.snippet):
o site do painel e a linha `import /etc/caddy/sites-agente/*.caddy`. No compose do Caddy,
monte `/srv/agente/caddy-sites:/etc/caddy/sites-agente:ro`, adicione a rede `agente-painel` e deixe o
Caddyfile montado **sem `:ro`** se quiser que as mudanças que o Jarvis propõe (e você aprova com o
código) possam ser aplicadas. Exemplo completo em `PRODUCAO.md`, passo 4.2. Depois:
```bash
docker compose up -d            # na pasta do Caddy
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

### Passo 6 — Primeiro acesso
Abra `https://agent.gabrielfdev.com`, entre com `ADMIN_PASSWORD`. Para receber avisos no celular,
instale o app **ntfy**, assine um tópico difícil de adivinhar e coloque em `NOTIFY_URL=https://ntfy.sh/<tópico>`.

### Passo 7 — Figma e Context7 (opcional, recomendado)
**Context7** (documentação atualizada das bibliotecas, evita o agente usar API antiga) já vem
registrado. Para limites maiores, crie uma chave grátis em context7.com e ponha em `CONTEXT7_API_KEY`.

**Figma** — o designer passa a criar o arquivo do projeto no seu Figma (variáveis, componentes e
telas) e o builder lê o design de lá. Precisa de um login OAuth único, feito de um terminal:
```bash
ssh -t seu-usuario@seu-vps 'cd /opt/agente-portfolio && sudo ./scripts/setup-figma.sh'
```
O script mostra uma URL: abra no **seu** navegador, autorize, e cole de volta a URL para a qual o
navegador foi redirecionado. Depois ponha `FIGMA_ENABLED=true` (e, se tiver mais de um plano,
`FIGMA_PLAN_KEY`) no `.env` e rode `docker compose up -d`. A página **Harness** do painel mostra o
estado de cada MCP, skill e plugin.

### Passo 8 — Teste de fumaça (recomendado antes do primeiro projeto real)
```bash
docker exec -it agente runuser -u agent -- claude -p "diga oi" --model claude-opus-5-5 --output-format json
```
Deve responder com `"is_error": false`. Depois crie um projeto pequeno no painel, por exemplo:
*"Uma página que converte temperaturas entre °C, °F e K, com histórico das últimas conversões."*
e acompanhe o ciclo inteiro uma vez antes de pedir algo grande.

### Passo 9 — Jarvis: comandar tudo pelo celular
```bash
sudo ./scripts/setup-jarvis.sh
```
Gera o token da API interna e o segundo fator (TOTP) para produção, sobe o container `jarvis`, faz o
login **completo** da sua assinatura (o Remote Control exige; o token do passo 2 continua só para os
workers) e abre a sessão para você ativar o Remote Control. Depois: app do Claude → **Code** →
**Jarvis** (a central) e **Jarvis · <Projeto>** (uma conversa por projeto). Digite `/` para ver os
comandos. Guia completo, segurança e exemplos em [`docs/JARVIS.md`](docs/JARVIS.md).

### Passo 10 — Conferir tudo
```bash
sudo ./scripts/verificar-vps.sh
```
Confere base, orquestrador (inclusive uma chamada real ao Opus pela assinatura), Caddy e domínios,
Jarvis (login, Remote Control, isolamento) e os projetos no ar. Cada falha vem com a correção. O
roteiro completo do VPS, com o que o agente pode e não pode mudar no servidor, está em
[`docs/VPS.md`](docs/VPS.md).

---

## 3. Uso no dia a dia

### Pedir um projeto novo
Pelo celular, fale com o **Jarvis** ("quero um app que…"): ele conduz as rodadas de perguntas com
você e acompanha o resto. Ou pelo painel → **Novo projeto**. Quanto melhor o pedido, melhores as
perguntas. Um bom pedido tem:
**problema** e público · **dados** (fonte, volume, frequência) · o que **precisa funcionar** para
ser útil · **integrações** e chaves · **referências** visuais ou de produto · o que faria o projeto
**se destacar** no portfólio.

> *Exemplo:* "Quero um ETL que baixa os dados abertos de carga horária da ONS desde 2015, organiza em
> camadas bronze/silver/gold em Parquet e mostra um dashboard com a curva de carga por subsistema,
> comparação ano a ano e detecção de anomalias. Atualização diária. Quero que recrutadores de
> engenharia de dados vejam linhagem, qualidade de dados e agendamento funcionando."

### Enquanto ele trabalha
- **Log ao vivo** na página do job (cada ferramenta que o agente usa aparece ali).
- **Mensagem para o agente:** o campo acima do log grava `STEER.md`; o hook entrega a mensagem na
  próxima ação dele. Use para corrigir rumo sem parar ("use DuckDB, não pandas").
- **Pausar** cria `AGENT_STOP` (todas as ferramentas passam a ser negadas) e encerra a sessão.
  **Tentar de novo** continua da mesma fase.

### Variáveis de ambiente e chaves dos projetos
Na página do projeto, cadastre as variáveis de staging e produção (ex.: `LLM_API_KEY`). O agente vê
só os **nomes** (via `.env.example`), nunca os valores. Elas valem no próximo deploy.

### Aprovar spec e plano
Depois das rodadas de perguntas, o agente para com a spec **e o plano técnico**: arquitetura
escolhida e por quê, ADRs, starter (ou "nenhum"), tickets e riscos. É a hora mais barata de mudar de
ideia — "prefiro SSR em vez de SPA", "sem login nesta versão". Peça ajustes em texto livre.

### Aprovar o design
Projetos com interface param depois da spec para você ver o design: screenshots desktop e mobile,
mockups navegáveis (abertos numa sandbox, sem script) e o link do Figma. Peça ajustes em texto livre
até gostar; a partir daí o design vira contrato do builder e referência do avaliador. Para pular essa
parada, `DESIGN_APPROVAL=false`.

### Mudanças e manutenção
Página do projeto → **Pedir mudança** ou **Relatar problema** (ou peça ao Jarvis, que investiga o
código antes e escreve o pedido com critérios de aceite). O botão **Rollback** volta produção para a
versão anterior na hora, sem passar pelo agente; pelo Jarvis, o rollback pede o código do autenticador.

### Integrar com a sua página de portfólio
`GET https://agent.gabrielfdev.com/public/portfolio.json` lista os projetos no ar (nome, descrição,
stack, URL, repositório). Sua página pode consumir isso e ficar sempre atualizada. Oculte um projeto
com o botão da página dele.

### Seus projetos que já estão no ar
Use **Adotar projeto** com a URL do GitHub (ou `/adotar <repositório>` no Jarvis). Antes da
aprovação do deploy, confira no staging que tudo funciona igual. Na hora de aprovar, o bloco antigo
desse domínio precisa sair do seu Caddyfile: peça `/caddy tira o bloco antigo do <domínio>`, aprove
com o código e a confirmação, e logo em seguida `/aprovar deploy` — o agente passa a servir o projeto.

---

## 4. Como melhorar o agente com o tempo

O resumo está aqui; o guia completo está em [`docs/MELHORIA.md`](docs/MELHORIA.md).

1. **Revise as lições** (painel → Lições) depois de cada job. Incorpore só o que teria evitado
   retrabalho real.
2. **Promova lições a sensores.** Se uma lição é verificável, transforme em teste, regra de lint,
   passo do `check.sh` ou hook — assim não depende do modelo lembrar.
3. **Calibre o avaliador.** Quando você achar no staging um problema que ele deixou passar (ou ele
   reprovar algo que estava bom), ajuste `harness/.claude/agents/evaluator.md` com esse exemplo.
4. **Olhe as métricas do painel:** rodadas médias, falhas de gate, reprovações, rollbacks. Rodadas
   subindo = harness piorando ou pedidos mais difíceis.
5. **A cada modelo novo, simplifique.** Desligue um componente por vez e veja se ele ainda é útil.
6. **Harness é código:** edite em `harness/` no repositório, commite, `git pull` no VPS. Como a pasta
   é montada no container, vale no próximo job — sem rebuild.
7. **Adicione ou troque skills pelo catálogo** (`harness/catalog.toml`): uma entrada diz de onde vem
   a skill (pasta própria ou repositório fixado por commit), quais papéis a recebem, para quais stacks
   e quem a carrega por inteiro. Depois `docker exec agente python -m orchestrator.skills sync`.
   Atualizar uma fonte externa = trocar o `ref` e rodar os testes.

---

## 5. Operação

| Tarefa | Comando (no VPS) |
|---|---|
| Logs do orquestrador | `docker logs -f agente` |
| Reiniciar | `cd /opt/agente-portfolio && docker compose restart` |
| Atualizar o agente | `git pull && docker compose build && docker compose up -d && sudo ./scripts/verificar-vps.sh --rapido` |
| Conferir se está tudo funcionando | `sudo ./scripts/verificar-vps.sh` |
| Atualizar o Claude Code | `docker compose build --no-cache && docker compose up -d` |
| Trocar o modelo | `AGENT_MODEL=` no `.env` + `docker compose up -d` |
| Backup diário | cron: `15 3 * * * /opt/agente-portfolio/scripts/backup.sh /var/backups/agente` |
| Ver projetos rodando | `docker ps --filter label=agente.project` |
| Entrar numa pasta de projeto | `docker exec -it -u agent agente bash -c 'cd /srv/agente/projects/<slug> && bash'` |
| Ver skills/MCPs disponíveis | painel → **Harness**, ou `docker exec agente python -m orchestrator.skills status` |
| Atualizar skills externas | troque o `ref` em `harness/catalog.toml` e rode `docker exec agente python -m orchestrator.skills sync` |
| (Re)conectar o Figma | `sudo ./scripts/setup-figma.sh` (token OAuth expirado ou revogado) |
| Ver a sessão do Jarvis no terminal | `docker exec -it -u jarvis jarvis tmux attach -t jarvis` (sair: `Ctrl+b` `d`) |
| Logs do Jarvis | `docker logs -f jarvis` |
| Refazer o login do Jarvis | `docker exec -it -u jarvis jarvis env HOME=/srv/jarvis/home claude auth login` |

Jobs que estavam rodando quando o container reiniciou voltam para a fila na mesma fase.

### Problemas comuns
- **`/health` público não responde no staging:** DNS curinga ainda não propagou, ou o Caddy não
  importa `sites-agente` / não está na rede `interna`. Veja `docker logs caddy`.
- **Senha no staging:** defina `STAGING_AUTH_PASSWORD` **e** `STAGING_AUTH_HASH` juntos. Se o
  avaliador não conseguir navegar com a senha, deixe o staging sem senha (ele já vai com `noindex`).
- **"limite de uso atingido":** normal na assinatura; o job volta sozinho depois de
  `RATE_LIMIT_BACKOFF_S`.
- **Mutation testing lento:** limite com `MUTATION_TIMEOUT_S`; os alvos ficam em `[tool.mutmut]` do
  `pyproject.toml` do projeto e precisam incluir a pasta `backend.domain` do manifesto (a regra de
  negócio). Em projeto adotado o mínimo começa em 0.
- **Plano técnico reprovado na validação:** a linha do tempo diz o que faltou (manifesto sem `start`,
  feature sem ticket, ticket apontando para arquivo inexistente…). Peça ajuste ou *Tentar de novo*.
- **Figma falhou no design:** o designer entrega os mockups locais mesmo assim e o motivo aparece na
  linha do tempo; normalmente é login expirado (rode o `setup-figma.sh`).
- **Job parado em "esperando você" com erro interno:** leia a linha do tempo; corrija e clique em
  *Tentar de novo* (a fase é refeita do início, o trabalho em arquivos é preservado).

---

## 6. Estrutura do repositório

```
harness/                     # o que guia e verifica o agente (instalado em cada projeto a cada job)
  CLAUDE.md                  # regras de trabalho, papéis e contrato de deploy
  catalog.toml               # skills, plugins e MCPs por papel e por stack (fontes fixadas por commit)
  skills/                    # 22 skills próprias (sabatina, plano-tecnico, tickets-verticais,
                             # arquitetura-livre, receitas dos starters, testes-que-importam, mutation,
                             # a11y, api, banco, segurança, observabilidade, design-system, figma, …)
  plugins/agente-lsp/        # Pyright + TypeScript: diagnósticos de tipo a cada edição
  .claude/settings.json      # permissões negadas + registro dos hooks
  .claude/hooks/             # guard, protect-files (propriedade por papel), kill-switch, track-read,
                             # verify-gate, post-edit-check, steer, stop-gate, commit-on-stop
  .claude/agents/            # planner, designer, builder, test-engineer, evaluator,
                             # security-reviewer, code-reviewer
  lessons/LESSONS.md         # semente das lições (as vivas ficam em /srv/agente/lessons)
  project-template/          # só o andaime: fixtures de teste guiadas pelo manifesto + gates
                             # (scripts/check.sh, test_quality.py, mutation.sh), .harness/, docs/adr/
  starters/                  # pontos de partida opcionais: python-fastapi, fastapi-react
orchestrator/                # pipeline (rodadas, plano, tickets), runner do Claude Code, biblioteca de
                             # skills, manifesto da stack (stack.py), integridade, API do Jarvis (api.py),
                             # TOTP, base de conhecimento, deploy, GitHub, monitor, painel, tickets.py
                             # (validação dos tickets) e infra.py (propostas no Caddyfile principal)
jarvis/                      # a central do celular: MCP "agente", sessões em segundo plano, hooks,
                             # workspace (CLAUDE.md-índice, comandos /, skills, subagentes), conversas
                             # por projeto (conversas.py), laboratório, supervisor
PRODUCAO.md                  # colocar em produção no VPS, do zero ao celular
docs/VPS.md                  # referência do VPS: quem muda o quê, cada verificação, credenciais
docs/JARVIS.md               # instalação e uso do Jarvis
docs/HARNESS.md              # o estudo de harness engineering e onde cada ideia está no código
docs/MELHORIA.md             # o ciclo de melhoria contínua
scripts/                     # install-vps.sh, setup-jarvis.sh, verificar-vps.sh, setup-figma.sh, backup.sh
tests/                       # 205 testes do próprio agente: pipeline, rodadas/plano/tickets, manifesto,
                             # hooks, integridade, segurança (links plantados, TOTP), skills, API,
                             # Jarvis (MCP por stdio de verdade, broker do laboratório),
                             # detector de testes fracos, runner, painel e verificar-vps.sh
```

Desenvolvimento local: `pip install -e '.[dev]' && pytest && ruff check .`
