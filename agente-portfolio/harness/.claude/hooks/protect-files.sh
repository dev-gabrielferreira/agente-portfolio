#!/usr/bin/env bash
# Guarda de propriedade: cada papel só escreve no que é dele.
#   - ninguém altera o harness, os gates ou segredos;
#   - o builder não altera os testes do test-engineer nem o design aprovado;
#   - o test-engineer só escreve testes; o designer só design; o planner só spec, glossário e plano.
source "$(dirname "$0")/_lib.sh"
path="$(jget '.tool_input.file_path')"
[[ -z "$path" ]] && path="$(jget '.tool_input.notebook_path')"
[[ -z "$path" ]] && exit 0
rel="$(relpath "$path")"

# ---- regras para todos os papéis
case "$rel" in
  /*)
    deny "escreva apenas dentro do projeto ($PROJECT_DIR)." ;;
  .claude/*|CLAUDE.md)
    deny "$rel faz parte do harness e é gerenciado pelo orquestrador. Se uma regra atrapalha, escreva a sugestão em .harness/HARNESS_FEEDBACK.md." ;;
  scripts/check.sh|scripts/test_quality.py|scripts/mutation.sh)
    deny "$rel é um gate de qualidade e não pode ser alterado por agentes. Se ele precisar de ajuste, descreva em .harness/HARNESS_FEEDBACK.md." ;;
  .mcp.json)
    deny "servidores MCP são configurados pelo orquestrador, não pelo projeto." ;;
  pytest.ini|tox.ini)
    deny "a configuração do pytest fica no pyproject.toml; um $rel paralelo mudaria quais testes rodam." ;;
  .env.example) ;;
  .env|.env.*)
    deny "arquivos .env guardam segredos e são gerenciados pelo painel. Documente variáveis novas em .env.example." ;;
esac

# mantenha em sincronia com orchestrator/integrity.py (o hook avisa na hora; o orquestrador garante)
TESTER_OWNED='^(tests/acceptance/|tests/e2e/|tests/properties/|tests/conftest\.py$|tests/test_api_contract\.py$)'

case "$ROLE" in
  builder)
    if [[ "$rel" =~ $TESTER_OWNED ]]; then
      deny "$rel pertence ao test-engineer: é a especificação executável e você não pode alterá-la. Faça o CÓDIGO passar. Se tiver certeza de que o teste contradiz a SPEC, registre em .harness/TEST_DISPUTES.md (teste, o que exige, o que a SPEC diz, evidência)."
    fi
    if [[ "$rel" == design/* ]]; then
      deny "$rel é o design aprovado. Implemente a partir dele; se algo for inviável, explique em .harness/PROGRESS.md e siga a alternativa mais próxima."
    fi ;;
  tester)
    if [[ ! "$rel" =~ ^(tests/|\.harness/) || "$rel" == tests/unit/* ]]; then
      deny "como test-engineer você só escreve em tests/acceptance, tests/e2e, tests/properties e .harness. Não corrija o código nem os testes do builder: registre o bug no relatório (campo bugs) com o teste que o demonstra."
    fi ;;
  designer)
    if [[ ! "$rel" =~ ^(design/|\.harness/) ]]; then
      deny "como designer você só escreve em design/ e .harness/. A implementação é do builder."
    fi ;;
  planner)
    if [[ ! "$rel" =~ ^(SPEC\.md$|CONTEXT\.md$|\.harness/|docs/) ]]; then
      deny "como planner você só escreve SPEC.md, CONTEXT.md, docs/ e .harness/. Não escreva código."
    fi ;;
esac
exit 0
