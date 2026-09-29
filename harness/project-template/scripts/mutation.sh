#!/usr/bin/env bash
# Mutation testing (mutmut) nos alvos de [tool.mutmut] do pyproject. Gerenciado pelo harness.
# Grava .harness/evidence/mutation.json e sai com erro se o score ficar abaixo de MIN_MUTATION_SCORE.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
MIN="${MIN_MUTATION_SCORE:-60}"
OUT=.harness/evidence/mutation.json
mkdir -p .harness/evidence
B=.venv/bin
[[ -x $B/mutmut ]] || { echo "mutmut não instalado (rode ./scripts/check.sh --fast antes)"; exit 1; }

targets=$($B/python - <<'EOF'
import tomllib
cfg = tomllib.load(open("pyproject.toml", "rb")).get("tool", {}).get("mutmut", {})
print(" ".join(cfg.get("source_paths", [])))
EOF
)
count=0
for t in $targets; do
  [[ -e $t ]] && count=$((count + $(find "$t" -name '*.py' ! -name '__init__.py' -size +0 | wc -l)))
done
if [[ $count -eq 0 ]]; then
  echo '{"skipped": true, "reason": "nenhum módulo de regra de negócio nos alvos do mutmut"}' > "$OUT"
  echo "mutation: sem alvos (${targets:-nenhum}); nada a medir"
  exit 0
fi

# mutation só mede uma suíte verde: com teste falhando o mutmut não tem linha de base
if ! $B/pytest -q -x -m "not e2e and not fuzz" -p no:cacheprovider --ignore=.claude --ignore=mutants >/dev/null 2>&1; then
  echo '{"blocked": true, "reason": "há testes falhando; mutation testing só mede uma suíte verde (rode de novo depois da correção do bug)"}' > "$OUT"
  echo "mutation: bloqueado — há testes falhando (bug reportado?). Rode de novo quando a suíte estiver verde."
  exit 2
fi

rm -rf mutants
timeout "${MUTATION_TIMEOUT_S:-1500}" $B/mutmut run >/dev/null 2>&1
rc=$?
[[ $rc -eq 124 ]] && echo "mutation: tempo esgotado; resultado parcial"
$B/mutmut export-cicd-stats >/dev/null 2>&1
survivors=$($B/mutmut results 2>/dev/null | grep -E ': (survived|no tests)' | sed 's/^ *//' | head -60)

MIN="$MIN" SURVIVORS="$survivors" $B/python - <<'EOF'
import json, os, sys
from pathlib import Path
stats = json.loads(Path("mutants/mutmut-cicd-stats.json").read_text()) if Path("mutants/mutmut-cicd-stats.json").exists() else {}
killed = stats.get("killed", 0) + stats.get("timeout", 0)
considered = stats.get("total", 0) - stats.get("skipped", 0)
measured = killed + stats.get("survived", 0) + stats.get("no_tests", 0) + stats.get("suspicious", 0)
if considered and not measured:
    Path(".harness/evidence/mutation.json").write_text(json.dumps({
        "blocked": True, **stats,
        "reason": "o mutmut não conseguiu rodar a suíte na área dele (veja 'mutmut run'); nada foi medido"}))
    print("mutation: bloqueado — o mutmut não executou a suíte; rode '.venv/bin/mutmut run' para ver o erro")
    sys.exit(2)
score = round(100 * killed / considered, 1) if considered else 0.0
survivors = [s for s in os.environ.get("SURVIVORS", "").splitlines() if s.strip()]
report = {"score": score, "min": int(os.environ["MIN"]), **stats, "survivors": survivors}
Path(".harness/evidence/mutation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
print(f"mutation: score {score}% (mínimo {os.environ['MIN']}%), {stats.get('survived', 0)} sobrevivente(s), "
      f"{stats.get('no_tests', 0)} sem teste")
for s in survivors[:20]:
    print("  vivo:", s, "→ veja com: .venv/bin/mutmut show", s.split(":")[0])
sys.exit(0 if score >= int(os.environ["MIN"]) else 1)
EOF
