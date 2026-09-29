"""Uma conversa do Jarvis por projeto: pastas geradas pelo root, seleção, scripts e supervisor."""

import json
import os
import shutil
import subprocess
import unicodedata
from pathlib import Path

from jarvis import conversas, hooks, skills_agente

REPO = Path(__file__).resolve().parents[1]


def note(folder: Path, slug: str, name: str, updated: str, status: str = "live") -> None:
    (folder / "projetos").mkdir(parents=True, exist_ok=True)
    (folder / "projetos" / f"{slug}.md").write_text(
        f"---\nprojeto: {slug}\nstatus: {status}\natualizado: {updated}\n---\n\n# {name}\n\ntexto\n"
    )


def central(tmp_path: Path) -> Path:
    c = tmp_path / "central"
    (c / ".claude").mkdir(parents=True)
    shutil.copytree(REPO / "jarvis" / "workspace" / ".claude" / "skills", c / ".claude" / "skills")
    shutil.copy(REPO / "jarvis" / "workspace" / ".claude" / "settings.json", c / ".claude" / "settings.json")
    (c / ".claude" / "agents").mkdir()
    for a in (REPO / "jarvis" / "agents").glob("*.md"):
        shutil.copy(a, c / ".claude" / "agents" / a.name)
    shutil.copy(REPO / "jarvis" / "workspace" / "CLAUDE.md", c / "CLAUDE.md")
    shutil.copy(REPO / "jarvis" / "workspace" / ".mcp.json", c / ".mcp.json")
    (c / "memoria").mkdir()
    return c


def test_projetos_mais_recentes_primeiro_sem_arquivados(tmp_path):
    k = tmp_path / "k"
    note(k, "loja", "Loja", "2026-09-20T10:00:00Z")
    note(k, "frete", "Frete", "2026-09-28T10:00:00Z")
    note(k, "velho", "Velho", "2026-01-01T10:00:00Z", status="archived")
    (k / "projetos" / "INVALIDO!.md").write_text("# x")
    got = conversas.projects(k)
    assert [p.slug for p in got] == ["frete", "loja"] and got[0].name == "Frete"
    assert [p.slug for p in conversas.wanted(got, 1, set())] == ["frete"]
    assert [p.slug for p in conversas.wanted(got, 1, {"loja"})] == ["loja"]  # fixo ocupa a vaga
    assert [p.slug for p in conversas.wanted(got, 0, {"loja"})] == ["loja"]  # fixo fica mesmo sem vaga


def test_pasta_do_projeto_tem_tudo_do_jarvis_e_diz_de_quem_e(tmp_path):
    c = central(tmp_path)
    root = tmp_path / "projetos"
    p = conversas.Project("loja", "Loja do Gabriel", "2026-09-28", "live")
    assert conversas.render(p, c, root, "claude-opus-5-5") is True
    d = root / "loja"
    text = (d / "CLAUDE.md").read_text()
    assert text.startswith((c / "CLAUDE.md").read_text().rstrip()[:200])  # mesmas regras da central
    assert "## Esta conversa é do projeto Loja do Gabriel (`loja`)" in text and "/srv/projetos/loja" in text
    settings = json.loads((d / ".claude" / "settings.json").read_text())
    assert (
        settings["model"] == "claude-opus-5-5"
        and settings["hooks"] == json.loads((c / ".claude" / "settings.json").read_text())["hooks"]
    )
    skills = sorted(x.name for x in (d / ".claude" / "skills").iterdir())
    assert skills == sorted(x.name for x in (c / ".claude" / "skills").iterdir())
    assert all((d / ".claude" / "skills" / s).is_symlink() for s in skills)
    assert (d / ".claude" / "agents" / "pesquisador.md").is_file() and (d / ".mcp.json").is_file()
    assert os.readlink(d / "memoria") == str(c / "memoria")
    assert conversas.MEMORY in settings["permissions"]["additionalDirectories"]  # memória sem pedir permissão
    assert conversas.render(p, c, root, "claude-opus-5-5") is False  # nada muda, nada é reescrito

    shutil.rmtree(c / ".claude" / "skills" / "conhecimento")  # skill removida da central some da pasta
    assert conversas.render(p, c, root, "claude-opus-5-5") is True
    assert not (d / ".claude" / "skills" / "conhecimento").exists()


def test_script_do_servidor_com_nome_do_projeto(tmp_path):
    p = conversas.Project("loja", "Loja do Gabriel", "", "live")
    script = conversas.run_script(p, tmp_path / "projetos", 2)
    assert "claude remote-control --name 'Jarvis · Loja do Gabriel'" in script
    assert "--spawn same-dir --capacity 2 --permission-mode acceptEdits" in script and "--model" not in script
    assert "--remote-control-session-name-prefix jarvis-loja" in script
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    k = tmp_path / "k"
    note(k, "x", 'Nome "com" $(aspas) `e` \\ barras', "2026")
    name = conversas.projects(k)[0].name
    assert not set('"`$\\') & set(name)


def test_sync_gera_pastas_e_scripts_dos_escolhidos(tmp_path, monkeypatch):
    k = tmp_path / "k"
    for i in range(4):
        note(k, f"p{i}", f"Projeto {i}", f"2026-09-2{i}")
    monkeypatch.setenv("JARVIS_CONVERSAS_MAX", "2")
    monkeypatch.setenv("JARVIS_CONVERSAS_FIXAS", "p0")
    chosen = list(conversas.sync(central(tmp_path), tmp_path / "projetos", tmp_path / "run", k))
    assert [p.slug for p in chosen] == ["p0", "p3"]
    assert sorted(x.name for x in (tmp_path / "run").iterdir()) == ["p0.sh", "p3.sh"]
    assert oct((tmp_path / "run" / "p0.sh").stat().st_mode)[-3:] == "755"


def test_skills_do_agente_entram_como_conhecimento(tmp_path):
    dest = tmp_path / "skills"
    shutil.copytree(REPO / "jarvis" / "workspace" / ".claude" / "skills", dest)
    copied = skills_agente.copy_all(REPO / "harness" / "skills", dest)
    assert "plano-tecnico" in copied and "tickets-verticais" in copied and "testes-que-importam" in copied
    assert "manutencao" not in copied  # a do Jarvis vale
    head = (dest / "plano-tecnico" / "SKILL.md").read_text().split("---")[1]
    assert "user-invocable: false" in head and "name: plano-tecnico" in head
    assert skills_agente.knowledge_only("sem frontmatter") == "sem frontmatter"
    risky = "---\nname: x\ndescription: y\nallowed-tools: Bash(*)\nhooks: {}\nmodel: haiku\n---\ncorpo\n"
    assert skills_agente.knowledge_only(risky) == "---\nname: x\ndescription: y\nuser-invocable: false\n---\ncorpo\n"


def test_hooks_da_conversa_de_projeto(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, "STATE", tmp_path / "state")
    monkeypatch.setattr(hooks, "PROJECTS_ROOT", tmp_path / "projetos")
    monkeypatch.delenv("JARVIS_CHANNEL", raising=False)
    (tmp_path / "projetos" / "loja" / "sub").mkdir(parents=True)
    assert hooks.project_of(tmp_path / "projetos" / "loja" / "sub") == "loja"
    assert hooks.project_of(tmp_path / "central") is None

    class Api:
        def __init__(self):
            self.events: list = []

        def get(self, path, **q):
            if path == "/resumo":
                return {"ultimo_evento": 0, "esperando_voce": [{"id": 9, "projeto": "frete"}]}
            if path == "/projetos/loja":
                jobs = [
                    {"id": 7, "tipo": "change", "status": "waiting", "fase": "production", "o_que_fazer": "aprovar"}
                ]
                return {
                    "nome": "Loja",
                    "status": "live",
                    "producao": "https://loja.x",
                    "versao_producao": "v3",
                    "jobs": jobs,
                }
            return {
                "eventos": [e for e in self.events if e["id"] > q["depois"]],
                "ultimo": max([0] + [e["id"] for e in self.events]),
            }

    api = Api()
    text = hooks.inicio(api, "loja")
    assert "Projeto desta conversa: Loja (`loja`)" in text and "job #7" in text and "outros projetos esperando" in text
    api.events = [
        {
            "id": 1,
            "tipo": "human",
            "mensagem": "aguardando você",
            "projeto": "frete",
            "job": 9,
            "esperando": "answers",
            "job_status": "waiting",
        },
        {
            "id": 2,
            "tipo": "human",
            "mensagem": "aguardando você",
            "projeto": "loja",
            "job": 7,
            "esperando": "deploy_approval",
            "job_status": "waiting",
        },
    ]
    got = hooks.eventos(api, "loja")
    assert "loja job #7" in got and "frete" not in got
    assert hooks.eventos(api, None) == ""  # cursor da central é outro: primeira vez só marca


FAKE_TMUX = r"""#!/usr/bin/env bash
echo "tmux $*" >> "$LOG"
case "$1" in
  has-session) [[ -f "$STATE/session" ]] ;;
  new-session) touch "$STATE/session"; echo _ > "$STATE/windows" ;;
  list-windows) n=0; while read -r w; do echo "@$n $w"; n=$((n+1)); done < "$STATE/windows" ;;
  new-window) name=""; while [[ $# -gt 0 ]]; do [[ "$1" == -n ]] && name="$2"; shift; done; echo "$name" >> "$STATE/windows" ;;
  kill-window) idx="${3#@}"; awk -v i="$idx" 'NR-1 != i' "$STATE/windows" > "$STATE/w2"; mv "$STATE/w2" "$STATE/windows" ;;
esac
"""


def test_supervisor_abre_e_fecha_janelas_por_projeto(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "tmux").write_text(FAKE_TMUX)
    (bin_ / "tmux").chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "log"
    script = f"""
source {REPO / "jarvis" / "supervisor.sh"} --only-functions
as_jarvis() {{ "$@"; }}
python3() {{
  if [[ " $* " == *" jarvis.trust "* ]]; then echo "trust ${{@: -1}}" >> "$LOG"; return 0; fi
  printf '%s\\n' $WANTED; [[ -n "${{INCOMPLETE:-}}" ]] || echo __ok__
}}
ensure_projects
"""
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "STATE": str(state), "LOG": str(log)}
    subprocess.run(["bash", "-c", script], env={**env, "WANTED": "loja frete"}, check=True)
    assert (state / "windows").read_text().split() == ["_", "loja", "frete"]
    assert (
        "new-window -d -t projetos: -n loja -c /srv/jarvis/projetos/loja bash /srv/jarvis/run/projetos/loja.sh"
        in log.read_text()
    )
    assert "trust /srv/jarvis/projetos/loja" in log.read_text()  # pasta confiável antes de abrir a conversa
    subprocess.run(["bash", "-c", script], env={**env, "WANTED": "frete novo"}, check=True)
    assert (state / "windows").read_text().split() == ["_", "frete", "novo"]
    subprocess.run(["bash", "-c", script], env={**env, "WANTED": "frete novo", "JARVIS_CONVERSAS": "0"}, check=True)
    assert (state / "windows").read_text().split() == ["_", "frete", "novo"]  # desligado: não mexe
    # sync que quebrou no meio: abre o que veio, mas não fecha nada
    subprocess.run(["bash", "-c", script], env={**env, "WANTED": "outro", "INCOMPLETE": "1"}, check=True)
    assert (state / "windows").read_text().split() == ["_", "frete", "novo", "outro"]
    assert "kill-window -t @" in log.read_text()  # fecha pelo id da janela


def test_uma_pasta_quebrada_nao_derruba_as_outras(tmp_path, monkeypatch):
    k = tmp_path / "k"
    note(k, "boa", "Boa", "2026-09-02")
    note(k, "ruim", "Ruim", "2026-09-01")
    (k / "projetos" / "estranha.md").write_bytes(b"---\nstatus: live\n---\n# \xff\xfe nome \xe2\x80\xae invertido\n")
    c = central(tmp_path)
    root = tmp_path / "projetos"
    (root / "ruim").mkdir(parents=True)
    (root / "ruim" / "CLAUDE.md").mkdir()  # algo impede gerar a pasta deste projeto
    warnings = []
    run = tmp_path / "run"
    run.mkdir()
    (run / "ruim.sh").write_text("#!/usr/bin/env bash\n")  # já estava no ar antes
    got = [p.slug for p in conversas.sync(c, root, run, k, warn=warnings.append)]
    assert got == ["boa", "ruim", "estranha"]  # "ruim" falhou ao gerar, mas continua no ar
    assert warnings and "ruim" in warnings[0]
    estranha = next(p for p in conversas.projects(k) if p.slug == "estranha")
    assert "\u202e" not in estranha.name and "invertido" in estranha.name  # sem caractere invisível de controle
    assert all(unicodedata.category(ch)[0] != "C" for ch in estranha.name)
    monkeypatch.setenv("JARVIS_CONVERSAS_MAX", "muitos")  # valor inválido não quebra: usa o padrão
    assert len(list(conversas.sync(c, root, run, k, warn=warnings.append))) == 3


def test_cada_conversa_tem_o_proprio_cursor_e_channel_nao_cala_projeto(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, "STATE", tmp_path / "state")
    assert hooks.cursor_name({"session_id": "abc-123"}, "loja") == "hook-cursor-abc-123"
    assert hooks.cursor_name({"session_id": "../../x"}, "loja") == "hook-cursor-x"
    assert hooks.cursor_name({}, "loja") == "hook-cursor-loja" and hooks.cursor_name({}, None) == "hook-cursor"

    class Api:
        def __init__(self):
            self.events = []

        def get(self, path, **q):
            if path == "/resumo":
                return {"ultimo_evento": 0}
            new = [e for e in self.events if e["id"] > q["depois"]]
            return {"eventos": new, "ultimo": max([q["depois"]] + [e["id"] for e in self.events])}

    api = Api()
    for name in ("hook-cursor-s1", "hook-cursor-s2"):
        hooks._save(name, 0)
    api.events = [{"id": 1, "tipo": "error", "mensagem": "falhou", "projeto": "loja", "job": 3}]
    assert "falha" in hooks.eventos(api, "loja", "hook-cursor-s1")
    assert "falha" in hooks.eventos(api, "loja", "hook-cursor-s2")  # a outra conversa também recebe
    monkeypatch.setenv("JARVIS_CHANNEL", "1")
    api.events.append({"id": 2, "tipo": "error", "mensagem": "de novo", "projeto": "loja", "job": 3})
    assert "de novo" in hooks.eventos(api, "loja", "hook-cursor-s1")  # projeto não tem channel: hook fala
    assert hooks.eventos(api, None, "hook-cursor-c") == ""  # central com channel: o MCP empurra
