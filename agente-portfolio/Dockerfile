# Uma imagem, dois containers (ver docker-compose.yml):
#   agente — orquestrador (root, fala com o Docker) + Claude Code dos papéis (usuário `agent`, sem Docker)
#   jarvis — sua central pelo celular: Claude Code interativo com Remote Control (usuário `jarvis`,
#            sem Docker, sem segredos do orquestrador, projetos só para leitura)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright AGENT_HOME=/srv/agente HARNESS_DIR=/opt/agente/harness \
    DISABLE_AUTOUPDATER=1 CHROMIUM_PATH=/usr/bin/chromium ENABLE_CLAUDEAI_MCP_SERVERS=false

# Ferramentas de sistema, Node 22 (Claude Code e Playwright MCP) e o cliente Docker + compose
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git jq util-linux build-essential tmux ripgrep qrencode \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian bookworm stable" \
        > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs docker-ce-cli docker-compose-plugin \
        chromium fonts-noto fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI, servidores MCP de navegador (Playwright e Chrome DevTools) e servidores de linguagem
# (Pyright, TypeScript) usados pelo plugin agente-lsp
# + detector de anti-padrões de design (impeccable, sem LLM) e o MCP de componentes do shadcn/ui
RUN npm install -g @anthropic-ai/claude-code @playwright/mcp chrome-devtools-mcp \
        pyright typescript typescript-language-server impeccable@4.1.0 shadcn@4.21.0 \
    && npx -y playwright install --with-deps chromium \
    && npm cache clean --force

# RTK (Rust Token Killer): compacta a saída de git/ls/testes antes de entrar no contexto — mais trabalho
# dentro do limite da assinatura. Binário oficial conferido pelo checksum publicado no release.
ARG RTK_VERSION=v0.50.0
RUN set -e; case "$(uname -m)" in x86_64) T=x86_64-unknown-linux-musl;; aarch64) T=aarch64-unknown-linux-gnu;; *) T=;; esac; \
    if [ -n "$T" ] && curl -fsSL -o /tmp/rtk.tgz "https://github.com/rtk-ai/rtk/releases/download/${RTK_VERSION}/rtk-$T.tar.gz" \
       && curl -fsSL -o /tmp/rtk.sums "https://github.com/rtk-ai/rtk/releases/download/${RTK_VERSION}/checksums.txt"; then \
      want="$(grep "rtk-$T.tar.gz" /tmp/rtk.sums | awk '{print $1}')"; got="$(sha256sum /tmp/rtk.tgz | awk '{print $1}')"; \
      if [ -n "$want" ] && [ "$want" = "$got" ]; then tar -xzf /tmp/rtk.tgz -C /usr/local/bin rtk && chmod 755 /usr/local/bin/rtk; \
      else echo "AVISO: checksum do RTK não confere; seguindo sem RTK"; fi; \
    else echo "AVISO: RTK indisponível para esta arquitetura/versão"; fi; rm -f /tmp/rtk.*
ENV RTK_TELEMETRY_DISABLED=1

# Bun: roda os plugins de channel oficiais (Telegram/Discord) se você ligar JARVIS_CHANNELS
RUN npm install -g bun && npm cache clean --force || echo "AVISO: bun não instalado (channels de chat ficam indisponíveis)"
# pnpm para projetos que o usam (o gate do frontend respeita o gerenciador declarado no manifesto)
RUN corepack enable && corepack prepare pnpm@latest --activate || echo "AVISO: pnpm indisponível"

# Ferramentas usadas pelos agentes: lint, semgrep (revisor de segurança) e o Agent SDK que o plugin
# oficial security-guidance usa para revisar diffs
RUN pip install ruff uv semgrep claude-agent-sdk

# Usuários isolados que executam o Claude Code: sem sudo, sem grupo docker
RUN useradd --create-home --uid 1001 --shell /bin/bash agent \
    && useradd --no-create-home --home-dir /srv/jarvis/home --uid 1002 --shell /bin/bash jarvis \
    && useradd --no-create-home --home-dir /srv/jarvis/lab-home --uid 1003 --shell /bin/bash lab \
    && mkdir -p "$PLAYWRIGHT_BROWSERS_PATH" && chown -R agent:agent "$PLAYWRIGHT_BROWSERS_PATH" \
    && git config --system init.defaultBranch main \
    && git config --system --add safe.directory '*'

WORKDIR /opt/agente
COPY pyproject.toml ./
COPY orchestrator ./orchestrator
COPY jarvis ./jarvis
RUN pip install . && chmod 755 jarvis/entrypoint.sh jarvis/supervisor.sh
COPY harness ./harness
# skills e plugins externos fixados no catálogo (o entrypoint repete o sync se o volume estiver vazio)
RUN AGENT_HOME=/opt/agente/seed python -m orchestrator.skills sync || true
COPY deploy/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 755 /usr/local/bin/entrypoint.sh

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "orchestrator"]
