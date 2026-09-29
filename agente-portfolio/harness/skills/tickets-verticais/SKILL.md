---
name: tickets-verticais
description: Quebra o plano em tickets de fatia vertical (tracer bullets) bem detalhados — objetivo, contexto, o que construir por camada, arquivos, contratos, critérios de aceite, costuras de teste e fora do escopo — e como o builder executa um ticket por sessão com TDD. Use no plano técnico (planner) e ao implementar um ticket (builder).
---

# Tickets em fatias verticais

Baseado em "to-tickets" de Matt Pocock (mattpocock/skills, MIT). Cada ticket é uma sessão do
builder com **contexto limpo**: ele não viu a sabatina, a conversa com o Gabriel nem o raciocínio do
plano. Só sabe o que está no ticket, no plano e no código. Então o ticket é um **briefing
completo**: um engenheiro competente que acabou de chegar precisa conseguir entregá-lo sem
adivinhar nada. Pequeno no escopo, generoso na descrição.

## Para o planner: escrever os tickets

`.harness/tickets.json` (o índice):

```json
{"tickets": [
  {"id": "T01", "title": "Fundação andando", "file": ".harness/tickets/T01-fundacao.md",
   "features": [], "blocked_by": [], "status": "todo"},
  {"id": "T02", "title": "Registrar leitura do chiller e calcular o COP", "file": ".harness/tickets/T02-leituras.md",
   "features": ["F01"], "blocked_by": ["T01"], "status": "todo"}
]}
```

Cada `.harness/tickets/<ID>-<slug>.md` segue o modelo abaixo. **Todas as seções são obrigatórias**
(o orquestrador confere: título, seções com conteúdo, 3+ critérios em checkbox, caminhos citados,
itens em costuras e fora do escopo, features citadas, nenhum "…"/"TBD"/"a definir"). Plano com
ticket raso volta para você corrigir antes de chegar ao Gabriel.

| Seção | O que responde |
|---|---|
| título + linha de metadados | `# T02 — …` e `**Features:** F01 · **Bloqueado por:** T01 · **Tamanho:** P/M/G` |
| `## Objetivo` | o que o usuário consegue fazer quando isto estiver pronto, e por que importa (2–4 frases) |
| `## Contexto` | o que já existe (tickets anteriores, arquivos, dados), ADRs e seções do PLAN que se aplicam, termos do `CONTEXT.md` |
| `## O que construir` | a fatia por camada — **Dados**, **Regra de negócio**, **API**, **Interface** (as que existirem), com nomes, campos, regras, estados de tela e mensagens de erro |
| `## Arquivos e módulos` | caminhos entre crases, cada um com "criar" ou "alterar" e o que muda |
| `## Contratos` | rotas (método, caminho, entrada, saída, erros), assinaturas das funções de domínio, exemplos de JSON. Sem contrato novo: diga qual existente é usado |
| `## Critérios de aceite` | checkboxes observáveis ("dado … quando … então …"), no mínimo 3: caminho feliz, erro e borda. Juntos, os tickets de uma feature cobrem **todos** os aceites dela |
| `## Costuras de teste` | onde cada teste toca: função pura, TestClient, navegador; e os casos de cada costura |
| `## Fora do escopo` | o que parece fazer parte mas não entra (e em que ticket entra) |
| `## Riscos e armadilhas` | o que costuma dar errado aqui (unidades, fuso, arredondamento, concorrência, dependência pesada) |

### Exemplo completo

```markdown
# T02 — Registrar leitura do chiller e calcular o COP

**Features:** F01 · **Bloqueado por:** T01 · **Tamanho:** M

## Objetivo

O técnico registra uma leitura do chiller (temperaturas de água gelada, vazão e potência elétrica)
e vê na hora o COP calculado. É o núcleo do produto: sem leituras confiáveis não existe painel de
eficiência, e a validação física das entradas é o que diferencia de uma planilha.

## Contexto

T01 entregou a fundação: FastAPI com `/health`, SQLite em `/data/app.db` via SQLAlchemy 2 e
Alembic, templates Jinja + HTMX (ADR 0002) e gates verdes. O cálculo segue o PLAN, seção
"Regra de eficiência": COP = carga térmica (kW) / potência elétrica (kW), com carga térmica =
vazão (m³/h) × 1,163 × ΔT (°C). "Leitura" e "COP" estão definidos no `CONTEXT.md`; não use
"medição" nem "eficiência" como sinônimos no código.

## O que construir

- **Dados:** tabela `readings` (id, chiller_tag, taken_at UTC, supply_temp_c, return_temp_c,
  flow_m3h, power_kw, cop, created_at). Migração Alembic nova. `cop` é gravado (histórico não muda
  se a fórmula mudar depois; ADR 0003).
- **Regra de negócio:** `compute_cop(reading_input) -> Decimal` em `app/domain/efficiency.py`,
  função pura. Rejeita ΔT ≤ 0, vazão ≤ 0 e potência ≤ 0 com `InvalidReading(campo, motivo)`.
  Arredonda o COP em 2 casas (ROUND_HALF_UP).
- **API:** `POST /api/readings` valida com Pydantic, chama `compute_cop`, grava e devolve 201.
- **Interface:** formulário em `/leituras/nova` (HTMX): campos com unidade no rótulo, erro ao lado
  do campo inválido sem apagar o que foi digitado, e após salvar mostra "COP 5,51" com a leitura
  no topo da lista.

## Arquivos e módulos

- criar `app/domain/efficiency.py` — `compute_cop` e `InvalidReading`
- criar `app/models/reading.py` e a migração em `migrations/versions/`
- criar `app/routes/readings.py` — rota da API e da página; registrar em `app/main.py` (alterar)
- criar `app/templates/readings/new.html` e o parcial `app/templates/readings/_result.html`

## Contratos

`POST /api/readings`
- entrada: `{"chiller_tag": "CH-01", "taken_at": "2026-09-29T14:00:00Z", "supply_temp_c": 7.0,
  "return_temp_c": 12.0, "flow_m3h": 180, "power_kw": 190}`
- 201: a leitura gravada com `id` e `cop` (ex.: `5.51`)
- 422: `{"detail": [{"field": "return_temp_c", "message": "retorno deve ser maior que a alimentação"}]}`

## Critérios de aceite

- [ ] Dado alimentação 7,0 °C, retorno 12,0 °C, vazão 180 m³/h e potência 190 kW, quando envio a leitura, então recebo 201 e o COP 5,51.
- [ ] Dado retorno menor ou igual à alimentação, quando envio, então recebo 422 apontando `return_temp_c` e nada é gravado.
- [ ] Dado vazão ou potência zero ou negativa, quando envio, então recebo 422 com o campo e a mensagem, e nada é gravado.
- [ ] Na página, ao salvar uma leitura válida, o COP aparece sem recarregar a página e a leitura entra no topo da lista.
- [ ] Na página, um campo inválido mostra o erro ao lado dele e mantém os valores digitados.

## Costuras de teste

- `compute_cop` (função pura): caso do exemplo, arredondamento na casa limite, cada entrada inválida; propriedade: COP cresce com ΔT mantidos vazão e potência.
- API pelo TestClient: 201 com corpo completo, 422 por campo, banco vazio depois de um 422.
- Navegador (e2e): preencher e salvar, erro inline mantendo os valores.

## Fora do escopo

- Editar ou apagar leitura (T04).
- Gráfico mensal de COP (T03).
- Importar leituras por CSV (T05).

## Riscos e armadilhas

- Decimal vs float: calcule em `Decimal` para o arredondamento bater com o critério.
- `taken_at` sem fuso: rejeite (422) em vez de assumir o fuso do servidor.
```

### Regras

- **T01 é sempre a fundação andando**: estrutura do projeto conforme o plano (ou o starter),
  `pyproject.toml` com o extra `dev` das ferramentas de teste, Dockerfile do contrato, `/health`,
  config por ambiente, frontend (se houver) com lint/tipos/testes/build rodando, gates verdes. Os
  critérios dele são verificáveis ("`docker build` conclui", "`GET /health` responde 200 em < 1 s").
- Fatia **vertical**: dado → regra → API → interface → teste. Nunca "todos os modelos" num ticket e
  "todas as rotas" noutro.
- Pequeno no escopo: cabe numa sessão (ordem de grandeza: algumas horas de um dev). Se o "O que
  construir" passa de uma tela e um fluxo, ou os critérios passam de ~8, quebre em dois.
- Detalhado na descrição: nomes de arquivos, funções, campos, rotas, mensagens e números de exemplo
  concretos. O builder implementa o que está escrito; o que não está escrito ele vai inventar.
- Critério de aceite é comportamento observável com dados de exemplo, nunca "código limpo" ou
  "funciona corretamente".
- Refatoração ampla → expandir (novo lado a lado), migrar em lotes, contrair (remover o velho).
- `blocked_by` só com dependência real; o que não depende pode vir em qualquer ordem.
- Toda feature de `features.json` coberta por ao menos um ticket, e todos os aceites dela
  distribuídos nos critérios desses tickets. `status` começa `todo`.

## Para o builder: executar um ticket

1. Leia o ticket inteiro, as seções do `docs/PLAN.md` e os ADRs que ele cita, e `CONTEXT.md`.
2. TDD nas costuras do ticket (skill `tdd`): um teste falhando para o primeiro critério → código
   mínimo → verde → próximo critério. Refatore ao final, com a suíte verde.
3. Respeite **Fora do escopo**. Se algo ali for necessário para um critério, o ticket está errado:
   registre em `.harness/PROGRESS.md` e em `.harness/HARNESS_FEEDBACK.md` e faça o mínimo.
4. Rode `./scripts/check.sh --fast` e depois o completo. Commits pequenos (`feat(T02): …`).
5. Atualize `.harness/PROGRESS.md`: uma linha por critério → teste que o comprova, decisões e
   pendências. Features só viram `passes: true` com evidência aberta (o hook confere).
6. Não faça o próximo ticket: o orquestrador abre uma sessão nova para ele.
