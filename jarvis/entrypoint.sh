#!/usr/bin/env bash
# Container do Jarvis: prepara pastas e usuários (como root), sobe o broker de sessões e entrega o
# controle ao supervisor. Três identidades, de propósito:
#
#   root    broker de sessões (socket só do grupo jarvis) e supervisor dos processos
#   jarvis  a sessão "Jarvis" (Remote Control, login completo, MCP `agente` com o token da API)
#   lab     laboratório: sessões em segundo plano e as abertas pelo celular. Não enxerga o token da
#           API nem os arquivos do jarvis — uma página maliciosa lida por uma pesquisa não aprova nada.
#
#   /srv/jarvis/home       HOME do jarvis (login do Claude, conversas)            jarvis 700
#   /srv/jarvis/central    pasta da sessão Jarvis (do root; só memoria/ é dele)    root 755
#   /srv/jarvis/projetos   uma pasta por projeto (conversa própria no app)        root 755
#   /srv/jarvis/run        scripts que o tmux executa                             root 755
#   /srv/jarvis/lab-home   HOME do lab (login/token próprios)                     lab 700
#   /srv/jarvis/lab        laboratório (git; entregas legíveis pelo Jarvis)        lab 755
#   /srv/jarvis/secrets    token só-modelo das sessões do lab                     root 700
#   /srv/projetos, /srv/conhecimento   somente leitura (volumes do agente)
set -euo pipefail
J=/srv/jarvis
SRC=/opt/agente/jarvis
HARNESS_SKILLS=/opt/agente/harness/skills
MODEL="${JARVIS_MODEL:-claude-opus-5-5}"
mkdir -p "$J"/{home,central,lab,lab-home,secrets,projetos,run}
chown root:root "$J/projetos" "$J/run"; chmod 755 "$J/projetos" "$J/run"
# copia de arquivo do root para pasta de outro usuário: apaga o destino antes (nunca escreve através
# de um link simbólico plantado lá)
put() { rm -f "$2"; cp "$1" "$2"; }

# 0) token só-modelo das sessões do laboratório: vai para um arquivo do root e sai do ambiente
if [[ -n "${LAB_CLAUDE_CODE_OAUTH_TOKEN:-}" ]]; then
  umask 077; printf '%s\n' "$LAB_CLAUDE_CODE_OAUTH_TOKEN" > "$J/secrets/lab-token"; umask 022
fi
unset LAB_CLAUDE_CODE_OAUTH_TOKEN
chown -R root:root "$J/secrets"; chmod 700 "$J/secrets"

# 1) regras do Jarvis: sempre a versão da imagem/repositório (ele não edita as próprias regras)
put "$SRC/workspace/CLAUDE.md" "$J/central/CLAUDE.md"
put "$SRC/workspace/.mcp.json" "$J/central/.mcp.json"
rm -rf "$J/central/.claude"
cp -r "$SRC/workspace/.claude" "$J/central/.claude"
mkdir -p "$J/central/.claude/agents"
cp -f "$SRC"/agents/*.md "$J/central/.claude/agents/"
# modelo das conversas abertas pelo app (modo servidor não repassa --model): Opus, como o resto
python3 - "$J/central/.claude/settings.json" "$MODEL" <<'PY'
import json, sys
path, model = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
data["model"] = model
json.dump(data, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
PY
# skills do agente (harness) como conhecimento em toda conversa: o Jarvis aplica sozinho quando
# escreve um pedido de mudança, revisa um plano ou discute testes; ficam fora do menu de comandos /
if [[ -d "$HARNESS_SKILLS" ]]; then
  python3 -m jarvis.skills_agente "$HARNESS_SKILLS" "$J/central/.claude/skills" || echo "AVISO: skills do agente não copiadas" >&2
fi
[[ -L "$J/central/memoria" ]] && rm -f "$J/central/memoria"   # memória é pasta de verdade, nunca link
[[ -d "$J/central/memoria" ]] || cp -r "$SRC/workspace/memoria" "$J/central/memoria"
ln -sfn /srv/conhecimento "$J/central/conhecimento"
chown -R jarvis:jarvis "$J/home" "$J/central/memoria"
chmod 700 "$J/home"
# a pasta central é do root: o jarvis não troca .claude/ nem planta arquivo que o root leia depois
chown root:root "$J/central"; chmod 755 "$J/central"
chown -R root:root "$J/central/.claude" "$J/central/CLAUDE.md" "$J/central/.mcp.json"
chmod -R a+rX,go-w "$J/central/.claude"

# 2) laboratório (usuário lab): repositório git com as regras dele e a pasta de entregas
put "$SRC/lab/CLAUDE.md" "$J/lab/CLAUDE.md"
rm -rf "$J/lab/.claude"
mkdir -p "$J/lab/.claude/agents"
python3 - "$SRC/lab/.claude/settings.json" "$J/lab/.claude/settings.json" "$MODEL" <<'PY'
import json, sys
src, dst, model = sys.argv[1:4]
data = json.load(open(src, encoding="utf-8"))
data["model"] = model  # o servidor do Laboratório também no Opus
json.dump(data, open(dst, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
PY
cp -f "$SRC"/agents/*.md "$J/lab/.claude/agents/"
[[ -L "$J/lab/entregas" ]] && rm -f "$J/lab/entregas"
mkdir -p "$J/lab/entregas" || true
[[ -e "$J/lab/entregas/.gitkeep" || -L "$J/lab/entregas/.gitkeep" ]] || touch "$J/lab/entregas/.gitkeep" || true
chown -R lab:lab "$J/lab" "$J/lab-home"
chmod 755 "$J/lab"; chmod 700 "$J/lab-home"
as_lab() { runuser -u lab -- env -i HOME="$J/lab-home" PATH="/usr/local/bin:/usr/bin:/bin" LANG=C.UTF-8 "$@"; }
as_jarvis() { runuser -u jarvis -- env HOME="$J/home" "$@"; }
# o repositório é do lab: se ele estiver num estado estranho, o boot segue (e avisa)
{
  [[ -d "$J/lab/.git" ]] || as_lab git -C "$J/lab" init -q -b main
  as_lab git -C "$J/lab" config user.name "Laboratório do Jarvis"
  as_lab git -C "$J/lab" config user.email "lab@localhost"
  as_lab git -C "$J/lab" add -A CLAUDE.md .claude entregas/.gitkeep
  as_lab git -C "$J/lab" diff --cached --quiet || as_lab git -C "$J/lab" commit -q -m "chore: sincroniza o laboratório com a imagem"
} || echo "AVISO: não consegui sincronizar o git do laboratório" >&2

# 3) pastas do harness confiáveis desde o boot (sem isso o Claude Code ignora as permissões e o MCP
#    do projeto). O "Enable Remote Control?" continua sendo você que aceita.
# a pasta-mãe dos projetos evita o diálogo; cada pasta de projeto é marcada pelo supervisor antes de
# abrir a conversa (as permissões do settings.json só valem na pasta exata marcada como confiável)
as_jarvis python3 -m jarvis.trust "$J/central" "$J/projetos" || echo "AVISO: não marquei a pasta do Jarvis como confiável" >&2
as_lab python3 -m jarvis.trust "$J/lab" || echo "AVISO: não marquei o laboratório como confiável" >&2

# 4) avisos que evitam surpresa
for var in CLAUDE_CODE_OAUTH_TOKEN ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC DISABLE_GROWTHBOOK; do
  if [[ -n "${!var:-}" ]]; then
    echo "AVISO: $var está definido no container do Jarvis e impede o Remote Control; remova-o do docker-compose" >&2
  fi
done
[[ -n "${JARVIS_API_TOKEN:-}" ]] || echo "AVISO: JARVIS_API_TOKEN vazio: o MCP 'agente' não vai conseguir falar com o orquestrador" >&2
[[ -s "$J/secrets/lab-token" ]] || echo "aviso: sem LAB_CLAUDE_CODE_OAUTH_TOKEN; sessões em segundo plano usam o login do usuário lab (se houver)" >&2
command -v rtk >/dev/null && echo "RTK $(rtk --version 2>/dev/null | head -1) ativo (economia de tokens nos comandos)"
echo "Claude Code $(as_jarvis claude --version 2>/dev/null || echo '?')"

exec "$@"
