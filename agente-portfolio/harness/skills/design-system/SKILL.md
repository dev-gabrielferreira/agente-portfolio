---
name: design-system
description: Como o designer cria a identidade e o design system de um projeto (conceito, tipografia, cor, espaçamento, componentes, estados) em tokens reutilizáveis e mockups de alta fidelidade. Use na fase de design e sempre que criar uma tela nova.
---

# Design system do projeto

## Entregáveis (pasta `design/`)

1. `DESIGN.md` — conceito em 2–3 frases ligado ao domínio (um painel de energia pode lembrar sala de
   controle; um app de manutenção, uma prancheta técnica), princípios, referências, e a lista de
   telas com o objetivo de cada uma.
2. `tokens.css` — variáveis CSS: cores (superfícies, texto, primária, estados sucesso/alerta/erro,
   com modo escuro), tipografia (no máximo 2 famílias; escala modular, ex. 1.25), espaçamento
   (base 4 px), raios, sombras, durações de animação.
3. `tokens.json` — os mesmos tokens em JSON (formato W3C Design Tokens) para o Figma e para o código.
4. `mockups/<tela>.html` — 3 a 5 telas-chave em HTML/CSS estático de alta fidelidade usando **só**
   os tokens, com **dados realistas** do domínio (nomes, números e datas plausíveis em PT-BR), estados
   vazio/carregando/erro onde fizer sentido, e versão mobile (375 px) funcional.
5. `components.md` — inventário de componentes (botão, campo, tabela, card, badge, gráfico…) com
   variantes e estados (hover, foco, desabilitado, erro).

## Três direções, uma entrega

Antes de desenhar telas, esboce **três direções visuais distintas** para este produto (conceito,
paleta, tipografia, densidade, um detalhe memorável). Use a skill `ui-ux-pro-max` para buscar
estilos, paletas e pares de fontes coerentes com o domínio, e a `impeccable`/`frontend-design`
para fugir dos vícios de interface gerada por IA. Critique as três contra a spec e o público,
escolha uma e desenvolva só ela. Em `DESIGN.md`, registre a escolhida e, em 2–3 linhas cada, as
descartadas (com um screenshot rápido em `.harness/evidence/design/alternativa-<letra>.png`) — o
Gabriel pode pedir uma delas na aprovação.

## Critérios (os mesmos do avaliador)

- **Coerência**: tudo parece do mesmo produto.
- **Originalidade**: decisões deliberadas; nada de gradiente roxo, grade de cards brancos iguais,
  hero genérico, emoji como ícone. Use as skills `frontend-design` e `web-design-guidelines`.
- **Ofício**: hierarquia tipográfica, alinhamento, contraste AA, espaçamento consistente.
- **Usabilidade**: a ação principal de cada tela é óbvia.

## Verificação

Sirva a pasta (`python -m http.server 8765 -d design/mockups &`), abra cada mockup com o Playwright
em 1366 px e 375 px, salve screenshots em `.harness/evidence/design/` e **olhe** para eles antes de
entregar. Rode o detector de anti-padrões `impeccable detect design/mockups` (sem LLM, 61 regras) e
corrija o que for pertinente — o mesmo detector roda no gate sobre a interface implementada.
Encerre o servidor ao terminar.
