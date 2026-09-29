"""Validação dos tickets que o planner escreve para o builder (orchestrator/tickets.py)."""

import re
from pathlib import Path

from orchestrator import tickets as tr
from tests.conftest import detailed_ticket

ROOT = Path(__file__).resolve().parents[1]


def skill_example() -> str:
    text = (ROOT / "harness" / "skills" / "tickets-verticais" / "SKILL.md").read_text(encoding="utf-8")
    return re.search(r"```markdown\n(.*?)\n```\n", text, re.S).group(1)


def test_exemplo_da_skill_passa_na_validacao():
    # a skill e o validador andam juntos: o exemplo que o planner copia precisa passar
    assert tr.check({"id": "T02", "features": ["F01"]}, skill_example()) == []


def test_secoes_faltando_sao_apontadas_uma_a_uma():
    text = skill_example().replace("## Fora do escopo", "## Observações").replace("## Contexto", "## Histórico")
    problems = tr.check({"id": "T02", "features": ["F01"]}, text)
    assert "ticket T02: falta a seção '## Fora do escopo'" in problems
    assert "ticket T02: falta a seção '## Contexto'" in problems


def test_titulo_sem_acento_ou_com_complemento_conta():
    text = skill_example().replace("## Critérios de aceite", "## Criterios de aceite (F01)")
    assert tr.check({"id": "T02", "features": ["F01"]}, text) == []


def test_poucos_criterios_ou_criterio_vago_reprovam():
    base = detailed_ticket("T03", "Curva", ["F01"])
    poucos = re.sub(r"- \[ \] Dado um mês sem dados.*\n", "", base)
    assert any("2 critério(s) de aceite" in p for p in tr.check({"id": "T03"}, poucos))
    vago = base.replace("- [ ] Dado agosto", "- [ ] Funciona bem.\n- [ ] Dado agosto")
    assert any("critério vago demais: 'Funciona bem.'" in p for p in tr.check({"id": "T03"}, vago))


def test_marcadores_em_aberto_reprovam_mas_codigo_de_exemplo_nao():
    base = detailed_ticket("T03", "Curva", [])
    assert any("marcador em aberto" in p for p in tr.check({"id": "T03"}, base + "\n- …\n"))
    assert any("marcador em aberto" in p for p in tr.check({"id": "T03"}, base.replace("Fuso:", "Fuso (TBD):")))
    code = base + "\n```python\nclass Repo(Protocol):\n    def get(self): ...\n    ...\n```\n"
    assert tr.check({"id": "T03"}, code) == []


def test_arquivos_sem_caminho_e_feature_nao_citada():
    base = detailed_ticket("T03", "Curva", ["F01"])
    sem_caminho = re.sub(
        r"## Arquivos e módulos\n.*?\n## ",
        "## Arquivos e módulos\n\n- o módulo de curva e a página inicial\n\n## ",
        base,
        flags=re.S,
    )
    problems = tr.check({"id": "T03", "features": ["F01", "F02"]}, sem_caminho)
    assert any("não cita nenhum caminho" in p for p in problems)
    assert "ticket T03: não cita a feature F02 que diz cobrir" in problems


def test_titulo_de_secao_dentro_de_bloco_de_codigo_nao_conta():
    base = detailed_ticket("T03", "Curva", []).replace("## Fora do escopo", "## Observações")
    fake = base + "\n```markdown\n## Fora do escopo\n- nada\n```\n"
    assert "ticket T03: falta a seção '## Fora do escopo'" in tr.check({"id": "T03"}, fake)


def test_cobertura_dos_aceites_por_feature():
    text = detailed_ticket("T02", "Curva", ["F01"])  # 3 critérios
    feats = [
        {"id": "F01", "acceptance": ["a", "b", "c", "d"], "passes": False},
        {"id": "F02", "acceptance": ["a"], "passes": True},
    ]
    assert tr.coverage([({"id": "T02", "features": ["F01"]}, text)], feats) == [
        "feature F01: 4 critério(s) de aceite na spec, mas os tickets que a cobrem somam 3"
    ]
    other = detailed_ticket("T03", "Curva 2", ["F01"])
    both = [({"id": "T02", "features": ["F01"]}, text), ({"id": "T03", "features": ["F01"]}, other)]
    assert tr.coverage(both, feats) == []
