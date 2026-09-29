---
name: mutation-testing-mutmut
description: Como medir se os testes pegam bugs com mutation testing (mutmut 3) e como matar mutantes sobreviventes que importam. Use depois de escrever testes de aceitação ou de propriedade, e sempre que precisar provar que uma suíte não é só "feita para passar".
---

# Mutation testing com mutmut

O mutmut altera o código de propósito (troca `>` por `>=`, `+` por `-`, remove um `return`, muda uma
constante) e roda os testes. Se os testes continuam verdes, o **mutante sobreviveu**: existe um bug
possível que a suíte não detecta. A porcentagem de mutantes mortos é a medida honesta da qualidade
dos testes — cobertura de 100% com asserts fracos dá mutation score baixo.

## Rodar

```bash
./scripts/mutation.sh            # roda mutmut nos alvos de [tool.mutmut] e gera .harness/evidence/mutation.json
.venv/bin/mutmut results         # lista sobreviventes
.venv/bin/mutmut show <mutante>  # mostra o diff do mutante
```

Os alvos ficam em `pyproject.toml` → `[tool.mutmut] source_paths` (padrão: `app/services`, onde
mora a regra de negócio). Não aponte para rotas e templates: o custo é alto e o sinal é fraco.

## Analisar sobreviventes

Para cada mutante vivo, pergunte: **se o código real tivesse essa mudança, o usuário perceberia?**

- **Sim** → falta um teste. Escreva o teste de comportamento que o mata (normalmente uma fronteira
  ou um valor exato que ninguém verificava). Não escreva teste que só existe para matar o mutante:
  ele precisa descrever uma regra da SPEC.
- **Não, é equivalente** (a mudança não altera comportamento observável, ex.: mensagem de log) →
  registre em `mutation.equivalent` no relatório com o motivo.
- **Não sei** → leia a regra na SPEC; se a SPEC não diz, anote como lacuna de especificação.

Se houver teste falhando (bug reportado), o `mutation.sh` responde "bloqueado": o mutation
testing só mede uma suíte verde. Registre o bug e rode de novo na próxima rodada.

Exemplo real: um teste `assert discount(100, True) is not None` matou 2 de 23 mutantes (9%). Uma
tabela com as fronteiras (0, 1000, 1000.01, VIP e não VIP) e o erro para total ≤ 0 matou 21 (91%).

## Meta

O gate exige `MIN_MUTATION_SCORE` (padrão 60%) nos alvos. Acima de 80% com mutantes restantes
justificados como equivalentes é um bom resultado. Mutante vivo em regra de dinheiro, permissão,
data ou dado persistido nunca é aceitável sem teste.
