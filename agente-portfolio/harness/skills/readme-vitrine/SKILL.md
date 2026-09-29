---
name: readme-vitrine
description: Como escrever o README de um projeto de portfólio para recrutadores técnicos — problema, demo, arquitetura, decisões, qualidade e como rodar. Use ao final de cada feature relevante e antes de cada deploy.
---

# README como vitrine

Quem lê: recrutador técnico com 2 minutos. Ordem:

1. **Título + uma frase** do problema que resolve (não da tecnologia).
2. **Link da demo** (`https://<slug>.gabrielfdev.com`) e um screenshot ou GIF (salve em
   `docs/screenshot.png`, gerado com Playwright).
3. **O que faz** — 3 a 6 bullets de funcionalidades concretas.
4. **Arquitetura** — diagrama Mermaid curto (fonte → processamento → armazenamento → interface).
5. **Decisões de engenharia** — 3 decisões interessantes com o porquê (links para
   `docs/DECISIONS.md`). Aqui aparece a maturidade: trade-offs, não lista de libs.
6. **Qualidade** — como é testado (aceitação por feature, fuzz de API, mutation score, e2e, a11y),
   com números reais do último gate.
7. **Stack** — uma linha.
8. **Rodar localmente** — comandos que funcionam de verdade.

Escreva no idioma da spec, frases curtas, sem superlativos vazios ("robusto", "escalável") — mostre
com números e decisões.
