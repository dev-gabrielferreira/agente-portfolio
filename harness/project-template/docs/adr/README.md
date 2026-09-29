# Decisões de arquitetura (ADRs)

Uma decisão por arquivo: `docs/adr/NNNN-titulo-curto.md` (0001, 0002…). Registre só o que é
**difícil de reverter, surpreendente para quem chega depois, ou uma troca real** entre alternativas.

```markdown
# NNNN — Título

- **Status:** aceita | substituída por NNNN
- **Contexto:** o problema e as forças em jogo (requisitos, restrições do VPS, prazos).
- **Alternativas consideradas:** 2–3, com o motivo de cada uma ter perdido.
- **Decisão:** o que foi escolhido.
- **Consequências:** o que fica mais fácil, o que fica mais difícil, quando revisitar.
```

Plataforma (não precisa de ADR, é contrato): um container com `Dockerfile` na raiz, porta 8000,
`GET /health`, dados em `/data`, configuração por variáveis de ambiente, logs em stdout.
