"""Configuração do orquestrador, lida do ambiente (arquivo .env no VPS)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "sim", "on"}


@dataclass
class Config:
    # Pastas (dentro do container; /srv/agente é um volume do host)
    base_dir: Path = field(default_factory=lambda: Path(_env("AGENT_HOME", "/srv/agente")))
    harness_dir: Path = field(default_factory=lambda: Path(_env("HARNESS_DIR", str(REPO_DIR / "harness"))))

    # Modelo e execução do Claude Code
    claude_bin: str = field(default_factory=lambda: _env("CLAUDE_BIN", "claude"))
    model: str = field(default_factory=lambda: _env("AGENT_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: _env("AGENT_EFFORT", "high"))
    eval_effort: str = field(default_factory=lambda: _env("AGENT_EVAL_EFFORT", "high"))
    run_as: str = field(default_factory=lambda: _env("AGENT_RUN_AS", "agent"))
    permission_mode: str = field(default_factory=lambda: _env("AGENT_PERMISSION_MODE", "bypassPermissions"))
    max_turns_build: int = field(default_factory=lambda: _int("MAX_TURNS_BUILD", 250))
    max_turns_review: int = field(default_factory=lambda: _int("MAX_TURNS_REVIEW", 120))
    session_timeout_s: int = field(default_factory=lambda: _int("SESSION_TIMEOUT_S", 4 * 3600))
    max_fix_rounds: int = field(default_factory=lambda: _int("MAX_FIX_ROUNDS", 4))
    # descoberta em rodadas (sabatina) e sessões por ticket antes de seguir para os gates
    discovery_rounds: int = field(default_factory=lambda: _int("DISCOVERY_ROUNDS", 3))
    ticket_attempts: int = field(default_factory=lambda: _int("TICKET_ATTEMPTS", 2))
    plan_fix_attempts: int = field(default_factory=lambda: _int("PLAN_FIX_ATTEMPTS", 1))
    rate_limit_backoff_s: int = field(default_factory=lambda: _int("RATE_LIMIT_BACKOFF_S", 3600))
    min_coverage: int = field(default_factory=lambda: _int("MIN_COVERAGE", 70))

    # Harness: fases opcionais e MCPs
    figma_enabled: bool = field(default_factory=lambda: _bool("FIGMA_ENABLED", False))
    figma_plan_key: str = field(default_factory=lambda: _env("FIGMA_PLAN_KEY"))
    design_approval: bool = field(default_factory=lambda: _bool("DESIGN_APPROVAL", True))
    max_test_rounds: int = field(default_factory=lambda: _int("MAX_TEST_ROUNDS", 3))
    min_mutation_score: int = field(default_factory=lambda: _int("MIN_MUTATION_SCORE", 60))
    mutation_timeout_s: int = field(default_factory=lambda: _int("MUTATION_TIMEOUT_S", 1800))
    chromium_path: str = field(default_factory=lambda: _env("CHROMIUM_PATH"))

    # Deploy
    domain: str = field(default_factory=lambda: _env("PORTFOLIO_DOMAIN", "gabrielfdev.com"))
    docker_network: str = field(default_factory=lambda: _env("DOCKER_NETWORK", "interna"))
    caddy_container: str = field(default_factory=lambda: _env("CADDY_CONTAINER", "caddy"))
    caddy_config_path: str = field(default_factory=lambda: _env("CADDY_CONFIG_PATH", "/etc/caddy/Caddyfile"))
    staging_auth_user: str = field(default_factory=lambda: _env("STAGING_AUTH_USER", "gabriel"))
    staging_auth_password: str = field(default_factory=lambda: _env("STAGING_AUTH_PASSWORD"))
    staging_auth_hash: str = field(default_factory=lambda: _env("STAGING_AUTH_HASH"))
    container_memory: str = field(default_factory=lambda: _env("CONTAINER_MEMORY", "512m"))
    container_cpus: str = field(default_factory=lambda: _env("CONTAINER_CPUS", "1.0"))
    health_timeout_s: int = field(default_factory=lambda: _int("HEALTH_TIMEOUT_S", 120))
    keep_images: int = field(default_factory=lambda: _int("KEEP_IMAGES", 4))
    deploy_dry_run: bool = field(default_factory=lambda: _bool("DEPLOY_DRY_RUN", False))

    # GitHub
    github_owner: str = field(default_factory=lambda: _env("GITHUB_OWNER", "dev-gabrielferreira"))
    github_token: str = field(default_factory=lambda: _env("GITHUB_TOKEN"))
    git_name: str = field(default_factory=lambda: _env("GIT_AUTHOR_NAME", "Agente Portfolio"))
    git_email: str = field(default_factory=lambda: _env("GIT_AUTHOR_EMAIL", "agente@localhost"))

    # Painel
    admin_password: str = field(default_factory=lambda: _env("ADMIN_PASSWORD"))
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET"))
    panel_https_only: bool = field(default_factory=lambda: _bool("PANEL_HTTPS_ONLY", True))

    # Jarvis (central de comando no celular): API com token próprio + segundo fator para produção
    jarvis_api_token: str = field(default_factory=lambda: _env("JARVIS_API_TOKEN"))
    admin_totp_secret: str = field(default_factory=lambda: _env("ADMIN_TOTP_SECRET"))
    # ações da API que exigem o código TOTP (deploy e rollback sempre deveriam estar aqui)
    jarvis_totp_for: str = field(default_factory=lambda: _env("JARVIS_TOTP_FOR", "deploy,rollback"))
    # a API só atende a rede interna do Docker; true libera pelo domínio público (não recomendado)
    jarvis_api_public: bool = field(default_factory=lambda: _bool("JARVIS_API_PUBLIC", False))
    # o Jarvis pode PROPOR mudanças no Caddyfile principal (aplicar exige sempre o código TOTP)
    caddy_proposals: bool = field(default_factory=lambda: _bool("CADDY_PROPOSALS", True))

    # Monitor e notificações
    monitor_interval_s: int = field(default_factory=lambda: _int("MONITOR_INTERVAL_S", 300))
    monitor_failures: int = field(default_factory=lambda: _int("MONITOR_FAILURES", 3))
    notify_url: str = field(default_factory=lambda: _env("NOTIFY_URL"))  # ex.: https://ntfy.sh/<tópico>
    panel_url: str = field(default_factory=lambda: _env("PANEL_URL", "https://agent.gabrielfdev.com"))

    @property
    def projects_dir(self) -> Path:
        return self.base_dir / "projects"

    @property
    def vendor_dir(self) -> Path:
        return self.base_dir / "vendor"

    @property
    def deploy_dir(self) -> Path:
        return self.base_dir / "deploy"

    @property
    def secrets_dir(self) -> Path:
        return self.base_dir / "secrets"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @property
    def knowledge_dir(self) -> Path:
        return self.base_dir / "knowledge"

    @property
    def caddy_sites_dir(self) -> Path:
        return self.base_dir / "caddy-sites"

    @property
    def infra_dir(self) -> Path:
        """Backups do Caddyfile principal antes de cada mudança aplicada."""
        return self.base_dir / "infra"

    @property
    def lessons_file(self) -> Path:
        return self.base_dir / "lessons" / "LESSONS.md"

    @property
    def db_path(self) -> Path:
        return self.base_dir / "agente.db"

    def host(self, slug: str, env: str) -> str:
        return f"{slug}.{self.domain}" if env == "production" else f"{slug}-staging.{self.domain}"

    def url(self, slug: str, env: str) -> str:
        return f"https://{self.host(slug, env)}"

    def ensure_dirs(self) -> None:
        for d in (
            self.projects_dir,
            self.deploy_dir,
            self.secrets_dir,
            self.logs_dir,
            self.caddy_sites_dir,
            self.vendor_dir,
            self.knowledge_dir,
            self.infra_dir,
            self.lessons_file.parent,
        ):
            d.mkdir(parents=True, exist_ok=True)
        self.secrets_dir.chmod(0o700)
        self.infra_dir.chmod(0o700)
        if not self.lessons_file.exists():
            seed = self.harness_dir / "lessons" / "LESSONS.md"
            self.lessons_file.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
