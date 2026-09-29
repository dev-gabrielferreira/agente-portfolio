"""Testes de propriedade (Hypothesis) para regras de negócio puras. Dono: test-engineer."""

from hypothesis import HealthCheck, settings

settings.register_profile(
    "gate", max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile("gate")
