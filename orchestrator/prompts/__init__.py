"""Modelos de tarefa. Usam string.Template ($variavel) para não conflitar com JSON/Markdown."""

from pathlib import Path
from string import Template

DIR = Path(__file__).parent


def render(_template: str, **values: object) -> str:
    text = (DIR / f"{_template}.md").read_text(encoding="utf-8")
    return Template(text).safe_substitute({k: ("" if v is None else str(v)) for k, v in values.items()})
