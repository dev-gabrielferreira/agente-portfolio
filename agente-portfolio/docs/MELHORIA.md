# Como melhorar o agente (o steering loop na prática)

> Todo componente do harness codifica uma suposição sobre o que o modelo **não** faz bem sozinho.
> Melhorar o agente é ajustar essas suposições com base em evidência: acrescentar controle onde ele
> erra de forma repetida e remover controle que deixou de ser necessário.

## A rotina

### Depois de cada job (5 minutos)
1. Abra o job e leia a **linha do tempo**. Onde houve rodada de correção, qual sensor reprovou?
2. Abra **Lições** e decida as propostas da retrospectiva:
   - incorpore as que teriam evitado retrabalho real e valem para outros projetos;
   - edite o texto para ficar curto, no imperativo e específico;
   - descarte lição genérica ("escreva código limpo") — ela só gasta contexto.
3. Se você achou no staging algo que o avaliador não achou, anote. Isso vira calibração (abaixo).

### Toda semana (30 minutos)
1. Compare as métricas do painel com a semana anterior: rodadas médias por job, falhas de gate,
   reprovações do avaliador, rollbacks.
2. Leia `LESSONS.md` inteiro. Lições que viraram teste/hook saem de lá. Lições contraditórias são
   unificadas. Mantenha abaixo de ~40 linhas.
3. Leia os arquivos `.harness/HARNESS_FEEDBACK.md` dos projetos: é o agente dizendo onde o harness
   atrapalhou.

### A cada modelo novo
Rode o mesmo pedido de referência (guarde um em `docs/`) com o harness completo e depois desligando
**um componente por vez**. Se o resultado não piora sem ele, remova. Candidatos típicos a ficar
obsoletos: regras de processo muito detalhadas no `CLAUDE.md`, o `stop-gate`, rodadas extras do
avaliador em projetos simples.

---

## A escada de promoção: de conselho para sensor

Quanto mais para baixo, mais confiável — e menos dependente do modelo lembrar.

| Nível | Onde | Exemplo |
|---|---|---|
| 1. Lição (texto) | `LESSONS.md` | "Declare rotas estáticas antes das rotas com parâmetro" |
| 2. Skill | `harness/.claude/skills/*` | seção "Rotas" na skill `stack-padrao` com exemplo |
| 3. Teste no template | `project-template/tests/` | teste que chama todas as rotas registradas e falha em 422 inesperado |
| 4. Passo do gate | `project-template/scripts/check.sh` | `ruff` com regra extra, `pytest --strict-markers`, checagem de migração |
| 5. Hook | `harness/.claude/hooks/*` | `post-edit-check.sh` avisando no mesmo instante em que o erro é escrito |

**Mensagens de sensor são prompts.** Quando criar um check, escreva a mensagem de erro para o
modelo: o que está errado, por que importa e o que fazer. Ex.: *"Rota `/items/reorder` declarada
depois de `/items/{id}`: o FastAPI vai capturar 'reorder' como id. Mova a rota estática para
cima."* Isso é injeção de prompt do bem — o agente se corrige sem gastar uma rodada.

### Exemplo real (aconteceu durante a construção deste agente)
Ao testar com o Claude Code 2.1.281 real, o planner com `tools:` no frontmatter perdia a saída
estruturada quando chamado com `--agent planner --json-schema`: a allowlist deixava de fora a
ferramenta de saída estruturada. A correção foi usar `disallowedTools` e, na mesma hora, promover a
lição ao nível 3: `tests/test_hooks.py::test_subagentes_nao_usam_allowlist_de_ferramentas` falha se
alguém voltar a usar `tools:`. O runner também ganhou um plano B que extrai o JSON do texto.

---

### Exemplo real 2 — o sensor ensinando o harness
Na primeira execução real do test-engineer, ele marcou as features com um alias
(`f01 = pytest.mark.feature("F01")` + `@f01`). O detector de rastreio não reconhecia a forma e o
agente gastou turnos reescrevendo os testes. Correção no sensor (aceitar alias, `pytestmark` e
marcador de classe) e teste novo em `tests/test_test_quality.py`. Na mesma execução ele notou que o
mutation testing não mede uma suíte com teste falhando — virou regra no `mutation.sh` ("bloqueado"
em vez de score 0) e uma linha na skill. É o ciclo: executar, ler o log, promover a lição.

### Exemplo real 3 — a brecha do shell
Numa execução real, o builder rodou `git stash push -- … scripts/mutation.sh scripts/test_quality.py`
para ver se o gate passava "sem as mudanças". Foi inofensivo (ele fez `stash pop`), mas mostrou que o
hook de proteção só enxerga as ferramentas Write/Edit: pelo shell dava para mexer em gates e nos testes
do test-engineer. Testando a hipótese, dois truques deixavam um teste de aceitação falhando fora do
gate — e um deles (um `conftest.py` em `tests/unit/` com `pytest_collection_modifyitems`) deixava o
gate **verde**. A lição foi direto para o nível 5, fora do alcance do modelo: `orchestrator/integrity.py`
compara o git antes/depois de cada sessão e desfaz o que o papel não podia escrever, os gates são
restaurados do harness antes de rodar, o `check.sh` ignora `addopts`/`omit` do projeto, e o
orquestrador exige que todo teste do test-engineer apareça executado no junit. Os dois truques viraram
testes em `tests/test_integrity.py`.

### Exemplo real 4 — quando o modelo tropeça no formato

No primeiro teste de ponta a ponta da sabatina com um modelo de verdade, as 6 perguntas vieram
*dentro* do campo de texto `understanding` (com tags), o schema recusou três vezes e, na quarta, o
modelo entregou um objeto mínimo ("teste?") só para passar — o job ficou esperando resposta para uma
pergunta sem sentido. Duas correções, uma de cada tipo: **sensor** — o runner guarda todas as
tentativas, conserta as que têm campos embutidos e fica com a mais completa que valida; **guia** —
o schema passou a trazer a lista primeiro e o prompt pede um campo por campo. Na execução seguinte:
7 perguntas na rodada 1, 3 na rodada 2, spec, glossário, plano com 5 ADRs e 10 tickets, e o builder
fechou o T01 com o gate verde (100% de cobertura) — sem nenhuma recusa de schema.

### Exemplo real 5 — ticket detalhado, conferido pela máquina

Pedido do Gabriel: "os tickets do builder devem ser bem detalhados". Virou as duas coisas de novo:
**guia** — a skill `tickets-verticais` ganhou o modelo completo (objetivo, contexto, o que construir
por camada, arquivos, contratos, critérios, costuras, fora do escopo, riscos) com um exemplo real;
**sensor** — `orchestrator/tickets.py` reprova ticket raso e devolve a lista ao planner antes de
chegar ao Gabriel, e um teste garante que o exemplo da skill passa no validador. No teste com o
Opus (pedido da calculadora de chillers): 6 perguntas, spec, 6 ADRs e 13 tickets de 5 a 10 mil
caracteres, com 6 a 8 critérios cada, aprovados pelo validador na primeira tentativa. O mesmo teste
mostrou um turno perdido: o planner tentou `git commit` (bloqueado de propósito); o prompt passou a
dizer que o checkpoint é do orquestrador.

### Exemplo real 6 — o revisor que não viu o código ser escrito

Ao liberar o Jarvis para propor mudanças no Caddyfile principal, um revisor independente (outra
sessão, sem o contexto de quem escreveu) achou o que os testes não pegavam: o código do autenticador
não estava preso à proposta — um Jarvis enganado por uma página poderia mostrar uma mudança e aplicar
outra com o código que você deu. Correção: uma confirmação calculada pelo orquestrador sobre aquela
proposta, que só chega no seu celular e no painel. O mesmo revisor achou um problema antigo: o
`python -m` dos hooks importava da pasta atual, onde o Jarvis pode escrever (`-P`/`PYTHONSAFEPATH` e
pastas do root resolveram). Lição para o harness: mudança que mexe em segurança passa por revisão de
quem não escreveu, e cada achado vira teste (`test_codigo_dado_para_uma_proposta_nao_aplica_outra`).

## Calibrar o test-engineer

Os mesmos passos do avaliador valem para ele: quando um bug chegar à produção, pergunte por que a
suíte não pegou. Quase sempre a resposta cabe em uma de três mudanças: um item novo na seção
"Onde procurar bugs" da skill `testes-que-importam`, um padrão novo no detector
(`scripts/test_quality.py`) ou um alvo novo no mutation testing (`[tool.mutmut]`).

## Calibrar o avaliador

O avaliador é o sensor mais valioso e o que mais precisa de ajuste. O viés dele é ser generoso.

1. Junte casos de divergência: *ele aprovou, mas estava ruim* e *ele reprovou, mas estava bom*.
2. Para cada caso, acrescente em `evaluator.md` um exemplo curto: a situação, a nota que ele deu e a
   nota certa com o porquê.
3. Se ele testa de forma rasa, deixe o foco mais explícito no `prompts/evaluate.md` (ex.: "recarregue
   a página após cada ação de escrita").
4. Se o critério de design está puxando tudo para o mesmo estilo, mude a redação da skill
   `frontend` — as palavras dos critérios moldam o resultado.

Limiares ficam em `orchestrator/schemas.py` (`SCORE_THRESHOLDS`) e são aplicados pelo orquestrador,
não pelo avaliador: mesmo que ele diga PASS, nota abaixo do limiar reprova.

## Ajustes de custo e tempo

| Sintoma | Ajuste |
|---|---|
| Limite da assinatura estourando | `AGENT_EVAL_EFFORT=medium`, `MAX_FIX_ROUNDS=3`, pedir projetos menores por job |
| Build demorando horas | pedido grande demais: quebre em "criar núcleo" + mudanças depois |
| Muitas rodadas por falha de gate | veja qual check falha mais e promova uma lição/hook para ele |
| Avaliador reprovando detalhe visual | ajuste a skill `frontend` e o limiar de `design` |
| Retrabalho por spec errada | responda às perguntas com mais detalhe; peça ajustes na spec antes de aprovar |

| Muitas rodadas de perguntas | `DISCOVERY_ROUNDS=2`; ou responda "pode seguir com as recomendações" |
| Tickets grandes demais (sessão estoura turnos) | ajuste a skill `tickets-verticais` com um exemplo do ticket que estourou; `TICKET_ATTEMPTS` controla quantas sessões um ticket ganha |
| Builder adivinhando o que o ticket não disse | aumente a régua em `orchestrator/tickets.py` (seções, mínimo de critérios, tamanho) e o exemplo da skill `tickets-verticais` junto: o teste `test_exemplo_da_skill_passa_na_validacao` garante que os dois concordam. `PLAN_FIX_ATTEMPTS` (padrão 1) diz quantas vezes o planner corrige sozinho antes de te chamar |
| Jarvis gastando limite | tudo roda no Opus por padrão; peça respostas mais curtas, menos sessões em paralelo, ou, se aceitar, uma sessão específica com outro modelo ("abre com o Sonnet"). RTK já compacta as saídas do Bash (`rtk gain` mostra a economia) |

## Melhorar o Jarvis

- As regras e skills dele ficam em `jarvis/workspace/` (e os subagentes em `jarvis/agents/`). Edite
  no repositório, `git pull` no VPS e `docker compose restart jarvis`: o container recopia tudo e a
  conversa continua (`--continue`).
- O que ele aprende sobre você fica em `/srv/jarvis/central/memoria/GABRIEL.md` (fora da imagem,
  sobrevive a atualizações). Leia de vez em quando e corte o que não vale mais.
- Quando ele errar o jeito de conduzir algo (perguntas demais, resumo longo, aprovar sem mostrar o
  que muda), corrija a skill correspondente — é o mesmo steering loop do agente, aplicado à central.

## Evoluções possíveis (quando fizer sentido)

- **Mais starters por topologia:** pipeline de dados (DuckDB + agendador + dashboard), API pura,
  site estático. Cada um é uma pasta em `harness/starters/` com `stack.json` — os gates já leem o
  manifesto, não precisam mudar.
- **Busca semântica na base de conhecimento:** quando as notas passarem de algumas dezenas, um índice
  híbrido (embeddings + palavra exata) para o Jarvis; até lá, índice + `rg` resolve melhor e mais barato.
- **Navegador de verdade para o Jarvis** (browser-use/browser-harness ou Playwright MCP com perfil
  próprio e contas só-leitura) para tarefas web recorrentes, como relatórios de métricas.
- **Sensor de drift contínuo:** job semanal por projeto para atualizar dependências e rodar
  `pip-audit`, abrindo mudança só se algo mudou (dá para agendar como tarefa recorrente do Jarvis).
- **Avaliação do próprio agente:** um conjunto fixo de 3–5 pedidos de referência rodados após mudanças
  grandes no harness, comparando rodadas, tempo e notas.
