"""Sensor contínuo de runtime: verifica /health dos projetos em produção e abre um job de incidente
quando um projeto falha várias vezes seguidas. O agente diagnostica e corrige, mas o deploy da
correção continua esperando a sua aprovação."""

from __future__ import annotations

import asyncio
import logging

from orchestrator.config import Config
from orchestrator.db import DB
from orchestrator.deployer import Deployer
from orchestrator.notify import Notifier
from orchestrator.pipeline import Pipeline

log = logging.getLogger(__name__)


class Monitor:
    def __init__(self, config: Config, db: DB, deployer: Deployer, pipeline: Pipeline, notifier: Notifier):
        self.c = config
        self.db = db
        self.deployer = deployer
        self.pipeline = pipeline
        self.notifier = notifier
        self.failures: dict[str, int] = {}
        self.last: dict[str, str] = {}

    async def loop(self) -> None:
        while True:
            try:
                await self.check_all()
            except Exception:
                log.exception("erro no monitor")
            await asyncio.sleep(self.c.monitor_interval_s)

    async def check_all(self) -> None:
        for project in self.db.projects():
            if project["status"] not in {"live", "down"} or not project["prod_tag"]:
                continue
            await self.check(project)

    async def check(self, project: dict) -> None:
        slug = project["slug"]
        ok, detail = await self.deployer.public_health(slug, "production")
        self.last[slug] = detail
        if ok:
            if self.failures.get(slug, 0) >= self.c.monitor_failures:
                await self.notifier.send(f"Recuperado: {project['name']}", "O /health voltou a responder 200.")
            self.failures[slug] = 0
            if project["status"] == "down":
                self.db.update_project(project["id"], status="live")
            return
        self.failures[slug] = self.failures.get(slug, 0) + 1
        if self.failures[slug] != self.c.monitor_failures:
            return
        self.db.update_project(project["id"], status="down")
        if self.db.active_jobs(project["id"]):
            await self.notifier.send(
                f"Fora do ar: {project['name']}",
                f"{detail}. Já existe um job em andamento; não abri incidente.",
                priority="urgent",
            )
            return
        logs = await self.deployer.logs(slug, "production", tail=150)
        request = (
            f"O monitor detectou {self.failures[slug]} falhas seguidas no /health de produção.\n"
            f"Último resultado: {detail}\n\nÚltimas linhas do log do container de produção:\n\n"
            f"```\n{logs[-8000:]}\n```"
        )
        job = self.pipeline.new_job(project["id"], "incident", request)
        await self.notifier.send(
            f"Fora do ar: {project['name']}",
            f"{detail}. Abri o incidente #{job['id']}; o agente já está investigando.",
            f"/jobs/{job['id']}",
            priority="urgent",
        )
