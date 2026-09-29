"""Esquemas JSON das saídas estruturadas (--json-schema) de cada papel."""

QUESTIONS = {
    "type": "object",
    # a lista vem primeiro: textos longos antes de arrays são onde o modelo às vezes tropeça no formato
    "properties": {
        "questions": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "why": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "default": {"type": "string", "description": "Sua recomendação"},
                },
                "required": ["id", "question", "why"],
            },
        },
        "project_name": {"type": "string", "description": "Nome curto e memorável do projeto"},
        "one_liner": {"type": "string", "description": "Descrição de uma linha para o portfólio"},
        "understanding": {"type": "string", "description": "O que você entendeu do pedido (3–5 frases)"},
    },
    "required": ["understanding", "questions"],
}

SPEC = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "features": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "title": {"type": "string"}},
                "required": ["id", "title"],
            },
        },
        "env_vars": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                    "secret": {"type": "boolean"},
                },
                "required": ["name", "purpose"],
            },
        },
        "stack": {"type": "string", "description": "Stack em poucas palavras, para o portfólio"},
        "stack_tags": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["ui", "react", "api", "data", "llm", "auth", "realtime", "scheduler", "mcp", "charts"],
            },
        },
        "needs_design": {"type": "boolean", "description": "Há telas novas ou mudança visual relevante"},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "features", "stack_tags", "needs_design"],
}

PLAN = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "O plano técnico em 3–6 frases (para o Gabriel aprovar)"},
        "architecture": {"type": "string", "description": "Componentes e como conversam, em poucas linhas"},
        "stack": {"type": "string", "description": "Stack em poucas palavras, para o portfólio"},
        "starter": {"type": "string", "description": "Starter escolhido (python-fastapi, fastapi-react) ou 'nenhum'"},
        "stack_tags": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["ui", "react", "api", "data", "llm", "auth", "realtime", "scheduler", "mcp", "charts"],
            },
        },
        "adrs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"file": {"type": "string"}, "title": {"type": "string"}, "decision": {"type": "string"}},
                "required": ["title", "decision"],
            },
        },
        "tickets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "title": {"type": "string"}},
                "required": ["id", "title"],
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "stack", "starter", "adrs", "tickets"],
}

SCORE_THRESHOLDS = {"functionality": 8, "product_depth": 7, "design": 7, "code_quality": 7, "accessibility": 8}

EVALUATION = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["PASS", "NEEDS_WORK"]},
        "scores": {
            "type": "object",
            "properties": {k: {"type": "integer", "minimum": 0, "maximum": 10} for k in SCORE_THRESHOLDS},
            "required": list(SCORE_THRESHOLDS),
        },
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "feature": {"type": "string"},
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                    "what": {"type": "string", "description": "O que acontece"},
                    "expected": {"type": "string", "description": "O que deveria acontecer"},
                    "where": {"type": "string", "description": "Tela, rota ou arquivo:linha"},
                },
                "required": ["severity", "what", "expected"],
            },
        },
        "features_verified": {"type": "array", "items": {"type": "string"}},
        "lighthouse": {
            "type": "object",
            "properties": {
                "performance": {"type": "number"},
                "accessibility": {"type": "number"},
                "best_practices": {"type": "number"},
                "seo": {"type": "number"},
            },
        },
    },
    "required": ["verdict", "scores", "summary", "findings"],
}

DESIGN = {
    "type": "object",
    "properties": {
        "concept": {"type": "string", "description": "O conceito visual em 2–3 frases"},
        "screens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "mockup": {"type": "string", "description": "caminho em design/mockups"},
                    "screenshots": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "mockup"],
            },
        },
        "figma_url": {"type": "string"},
        "figma_file_key": {"type": "string"},
        "figma_error": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["concept", "screens"],
}

TESTS = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "tests_written": {"type": "integer"},
        "bugs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "feature": {"type": "string"},
                    "test": {"type": "string", "description": "caminho::nome do teste que demonstra o bug"},
                    "observed": {"type": "string"},
                    "expected": {"type": "string"},
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                },
                "required": ["test", "observed", "expected", "severity"],
            },
        },
        "mutation": {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "equivalent": {"type": "array", "items": {"type": "string"}},
            },
        },
        "disputes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "test": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["kept", "fixed"]},
                    "reason": {"type": "string"},
                },
                "required": ["test", "verdict", "reason"],
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "bugs"],
}

CODE_REVIEW = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                    "title": {"type": "string"},
                    "where": {"type": "string"},
                    "impact": {"type": "string"},
                    "fix": {"type": "string"},
                    "source": {"type": "string", "description": "revisor que encontrou (ex.: silent-failure-hunter)"},
                },
                "required": ["severity", "title", "fix"],
            },
        },
    },
    "required": ["summary", "findings"],
}

SECURITY = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                    "title": {"type": "string"},
                    "where": {"type": "string"},
                    "scenario": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "title", "fix"],
            },
        },
    },
    "required": ["summary", "findings"],
}

RETRO = {
    "type": "object",
    "properties": {
        "lessons": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Regra curta, acionável, no imperativo"},
                    "kind": {"type": "string", "enum": ["rule", "skill", "check", "hook"]},
                    "evidence": {"type": "string", "description": "O que no histórico motiva a lição"},
                },
                "required": ["text", "kind", "evidence"],
            },
        },
        "simplifications": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Partes do harness que pareceram não agregar neste job",
        },
    },
    "required": ["lessons"],
}


def evaluation_passes(report: dict) -> tuple[bool, list[str]]:
    """Aplica os limiares no orquestrador: não confia só no veredito do avaliador."""
    reasons: list[str] = []
    if report.get("verdict") != "PASS":
        reasons.append("avaliador pediu ajustes")
    for key, minimum in SCORE_THRESHOLDS.items():
        score = (report.get("scores") or {}).get(key)
        if score is None or score < minimum:
            reasons.append(f"{key}={score} abaixo do mínimo {minimum}")
    blockers = [f for f in report.get("findings", []) if f.get("severity") == "blocker"]
    if blockers:
        reasons.append(f"{len(blockers)} achado(s) bloqueante(s)")
    return not reasons, reasons
