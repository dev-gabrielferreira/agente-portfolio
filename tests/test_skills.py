import json
import re
import shutil
import tomllib

import pytest

from orchestrator.config import REPO_DIR
from orchestrator.skills import AGENT_ROLE, ROLES, Library

HARNESS = REPO_DIR / "harness"


@pytest.fixture
def lib(tmp_path):
    return Library(HARNESS, tmp_path / "vendor", {})


def test_catalogo_valido_e_skills_locais_bem_formadas(lib):
    for item in lib.skills:
        if item.source != "local":
            continue
        text = (lib.item_dir(item) / "SKILL.md").read_text()
        front = text.split("---")[1]
        assert re.search(rf"^name: {re.escape(item.name)}$", front, re.M), item.name
        desc = re.search(r"^description: (.+)$", front, re.M).group(1)
        assert 40 < len(desc) <= 1024, item.name
    assert set(AGENT_ROLE.values()) <= set(ROLES)


def test_toda_skill_local_da_pasta_esta_no_catalogo(lib):
    in_catalog = {s.name for s in lib.skills if s.source == "local"}
    on_disk = {p.name for p in (HARNESS / "skills").iterdir() if p.is_dir()}
    assert on_disk == in_catalog


def test_roteamento_por_papel_e_tag(lib):
    api_only = lib.project_tags(["api"])
    ui = lib.project_tags(["ui"])
    builder_api = {s.name for s in lib.skills_for("builder", api_only)}
    assert "arquitetura-livre" in builder_api and "frontend" not in builder_api
    assert "tdd" in lib.preload_for("builder", api_only) or not lib.available(
        next(x for x in lib.skills if x.name == "tdd")
    )
    assert "receita-fastapi-react" not in builder_api and "receita-fastapi-react" in {
        s.name for s in lib.skills_for("builder", lib.project_tags(["react"]))
    }
    assert "sabatina" in lib.preload_for("planner", api_only)
    assert "frontend" in {s.name for s in lib.skills_for("builder", ui)}
    assert {s.name for s in lib.skills_for("tester", ui)} >= {
        "testes-que-importam",
        "mutation-testing-mutmut",
        "acessibilidade",
    }
    assert "testes-que-importam" in lib.preload_for("tester", ui)
    assert "figma" not in ui  # Figma desabilitado
    assert "figma" in Library(HARNESS, lib.vendor_dir, {"FIGMA_ENABLED": "true"}).project_tags(["ui"])


def test_instala_skills_e_renderiza_preload_dos_agentes(lib, tmp_path):
    proj = tmp_path / "proj"
    shutil.copytree(HARNESS / ".claude", proj / ".claude")
    manifest = lib.install(proj, lib.project_tags(["ui"]))
    assert (proj / ".claude/skills/testes-que-importam/SKILL.md").exists()
    assert (proj / ".claude/skills/frontend/SKILL.md").exists()
    assert "testes-que-importam" in manifest["tester"]
    tester = (proj / ".claude/agents/test-engineer.md").read_text()
    assert "skills: testes-que-importam, mutation-testing-mutmut" in tester
    for agent in (proj / ".claude/agents").glob("*.md"):
        assert "{{SKILLS}}" not in agent.read_text(), agent.name
    evaluator = (proj / ".claude/agents/evaluator.md").read_text()
    assert "\nskills:" not in evaluator.split("---")[1]  # sem preload: linha removida


def test_mcp_por_papel(lib, tmp_path):
    assert set(lib.mcp_config("evaluator", "/chrome")["mcpServers"]) == {"playwright", "chrome-devtools"}
    assert lib.mcp_config("evaluator", "")["mcpServers"].keys() == {"playwright"}  # sem Chromium, sem DevTools
    assert lib.mcp_config("security")["mcpServers"] == {}
    denied_security = lib.mcp_disallowed("security")
    assert "mcp__playwright" in denied_security and "mcp__figma" in denied_security
    assert "mcp__context7" not in denied_security
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"intruso": {"command": "x"}}}))
    assert "mcp__intruso" in lib.mcp_disallowed("builder", tmp_path)
    figma_on = Library(HARNESS, lib.vendor_dir, {"FIGMA_ENABLED": "1"})
    builder = figma_on.mcp_disallowed("builder")
    assert "mcp__figma" not in builder and "mcp__figma__use_figma" in builder
    assert not [d for d in figma_on.mcp_disallowed("designer") if d.startswith("mcp__figma")]


def test_fontes_externas_fixadas_por_commit():
    data = tomllib.loads((HARNESS / "catalog.toml").read_text())
    for name, src in data["sources"].items():
        if name != "local":
            assert re.fullmatch(r"[0-9a-f]{40}", src["ref"]), name
            assert src.get("license"), name


def test_skill_duplicada_e_rejeitada(tmp_path):
    h = tmp_path / "h"
    shutil.copytree(HARNESS, h, ignore=shutil.ignore_patterns("project-template"))
    cat = h / "catalog.toml"
    cat.write_text(
        cat.read_text()
        + '\n[[skills]]\nname = "frontend"\nsource = "local"\npath = "skills/frontend"\nroles = ["builder"]\n'
    )
    with pytest.raises(ValueError, match="duplicadas"):
        Library(h, tmp_path / "v", {})


def test_skills_de_terceiros_ficam_fora_do_repositorio_publico(tmp_path):
    vendor = tmp_path / "vendor"
    lib = Library(HARNESS, vendor, {})
    src = lib.source_root("anthropic-skills") / "skills" / "frontend-design"
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text("---\nname: frontend-design\ndescription: interfaces com identidade\n---\n")
    proj = tmp_path / "proj"
    shutil.copytree(HARNESS / ".claude", proj / ".claude")
    lib.install(proj, lib.project_tags(["ui"]))
    ignore = (proj / ".claude/skills/.gitignore").read_text()
    assert "/frontend-design/" in ignore and "/testes-que-importam/" not in ignore
    assert "Licença: Apache-2.0" in (proj / ".claude/skills/frontend-design/NOTICE.agente.md").read_text()
