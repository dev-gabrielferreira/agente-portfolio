---
name: testes-unitarios
description: Como o builder testa o próprio código (TDD de unidade, pytest, TestClient) e gera a evidência exigida pelo contrato default-FAIL. Use ao implementar ou corrigir qualquer feature. Os testes de aceitação são do test-engineer.
---

# Testes do builder

Existem dois donos de testes neste projeto:

| Pasta | Dono | Pode editar? |
|---|---|---|
| `tests/unit/` (fixtures suas em `tests/unit/conftest.py`) | **você (builder)** | sim |
| `tests/acceptance/`, `tests/e2e/`, `tests/properties/`, `tests/conftest.py`, `tests/test_api_contract.py` | **test-engineer** | **não** — hook bloqueia e o orquestrador desfaz alterações feitas pelo shell |

Os testes do test-engineer são a especificação executável. Seu trabalho é fazer o **código** passar
neles. Se você tiver certeza de que um deles está errado (contradiz a SPEC), registre em
`.harness/TEST_DISPUTES.md`: teste, o que ele exige, o que a SPEC diz, sua evidência. O
test-engineer julga a disputa. Nunca "conserte" um teste alheio pelo código (ex.: detectar ambiente
de teste e responder diferente) — isso é fraude e o revisor procura por isso.

## Seus testes (unidade e integração leve)

- **Red → green → refactor** para regra de negócio: escreva o teste que falha, veja falhar pelo
  motivo certo, implemente o mínimo, refatore.
- Teste `services/` sem HTTP; teste rotas com `TestClient` e banco temporário (fixture `client`).
- Afirme o **resultado observável** (valor, estado persistido, resposta), não detalhes internos.
- Um bug corrigido sempre ganha um teste que falhava antes da correção.
- Nada de rede real: `respx` para HTTP externo, fake para LLM.

## Evidência para o contrato default-FAIL

```bash
.venv/bin/pytest -q --junitxml=.harness/evidence/junit.xml
./scripts/check.sh --fast
```

Abra `.harness/evidence/junit.xml` (ou `check-report.txt`) com Read e só então marque
`"passes": true`. Para features visuais, gere screenshot com Playwright em `.harness/evidence/`.

## O que não fazer

- `assert resultado is not None` como única verificação; `assert True`; teste sem assert.
- `@pytest.mark.skip`/`xfail` para esconder falha. `time.sleep` para "esperar dar certo".
- Calcular o valor esperado com a mesma função que está sendo testada.
- Mockar a própria unidade que você está testando.

O gate (`scripts/test_quality.py`) reprova esses padrões automaticamente.
