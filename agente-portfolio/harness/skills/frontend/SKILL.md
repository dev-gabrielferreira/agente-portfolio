---
name: frontend
description: Princípios de design de interface para projetos de portfólio com identidade própria, evitando o visual genérico de IA. Use ao criar ou alterar telas, CSS ou componentes.
---

# Frontend com identidade

O avaliador pontua **design** com limiar 7/10 e penaliza visual de template. Estes são os critérios.

1. **Coerência** — cores, tipografia, espaçamento e ícones formam uma identidade única, ligada ao
   domínio do projeto (um painel de energia pode lembrar sala de controle; um app de manutenção,
   uma prancheta técnica).
2. **Originalidade** — decisões deliberadas. Evite: gradiente roxo/azul sobre branco, grade de
   cards brancos idênticos com sombra, hero centralizado genérico, ícone em círculo colorido para
   tudo, emojis como ícone.
3. **Ofício** — hierarquia tipográfica clara (no máximo 2 famílias), escala de espaçamento
   consistente (4/8 px), contraste AA, estados de hover/foco/disabled/loading/vazio/erro.
4. **Usabilidade** — a ação principal de cada tela é óbvia; o usuário entende o que fazer sem
   tutorial; formulários dizem o que está errado; funciona no celular (teste a 375 px).

## Prática

- Defina tokens em `:root` (cores, raios, espaçamentos, fontes) e use só eles.
- Dados antes de enfeite: gráficos com eixos e unidades, tabelas alinhadas, números com formato
  brasileiro quando a interface for em PT-BR.
- Estados vazios ensinam o próximo passo. Carregamento nunca trava a tela sem feedback.
- Tire screenshot com Playwright (desktop e 375 px) e **olhe** antes de dar a feature por pronta.
