---
name: receita-fastapi-react
description: Receita do starter fastapi-react — API FastAPI em /api + SPA React 19/TypeScript/Vite servida pelo mesmo container, estado de servidor, testes Vitest, ESLint e build multi-stage. Use quando o plano escolheu esse starter ou uma SPA React.
---

# Receita: FastAPI + React/Vite (starter fastapi-react)

## Estrutura

```
app/                 # FastAPI: /health, API em /api (APIRouter), SPA servida por rota GET de fallback
  services/          # regra de negócio pura (backend.domain → mutation testing)
frontend/
  package.json       # scripts: dev, build, typecheck, lint, test (vitest run)
  vite.config.ts     # proxy /api → :8000 em dev; config do Vitest (jsdom)
  eslint.config.js   # ESLint 10 + typescript-eslint + react-hooks
  src/main.tsx App.tsx App.test.tsx setupTests.ts styles.css
Dockerfile           # estágio node (npm ci + build) → estágio python servindo frontend/dist
```

O gate roda, a partir do manifesto: dependências (`npm ci`), `npm run lint`, `npm run typecheck`,
`npm test`, `npm run build` e reprova `.only`/`.skip` em testes. Versione o `package-lock.json`.

## Padrões

- API tipada: gere tipos do OpenAPI (`openapi-typescript`) em vez de escrever à mão; o backend é a
  fonte da verdade do contrato.
- Estado de servidor com TanStack Query (cache, loading, erro, retry); estado local com `useState`.
  Store global só com motivo registrado no plano.
- Rotas do cliente com React Router; o fallback do FastAPI devolve `index.html` para qualquer GET
  que não seja `/api/*`, `/health` ou arquivo do build.
- Estilo a partir de `design/tokens.css` (copie para `frontend/src/tokens.css` e importe no
  `main.tsx`). Para componentes acessíveis prontos, shadcn/ui (MCP `shadcn`) — adaptando os tokens,
  nunca o visual padrão da biblioteca.
- Testes do builder: Vitest + Testing Library por comportamento visível (papel/texto), nunca por
  detalhe de implementação; `fetch` mockado na borda. Fluxos completos são e2e do test-engineer.
- Acessibilidade (skill `acessibilidade`): semântica, foco visível, rótulos, contraste.
- Veja também `react-best-practices` e `composition-patterns` (Vercel).
