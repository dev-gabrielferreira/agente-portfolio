---
name: acessibilidade
description: Como garantir e testar acessibilidade (WCAG 2.2 AA) nas telas — HTML semântico, teclado, contraste, formulários, e testes automáticos com axe via Playwright. Use ao criar telas, ao escrever testes e2e e ao avaliar interface.
---

# Acessibilidade (WCAG 2.2 AA)

## Construir certo

- HTML semântico: `button` para ação, `a` para navegação, `label for` em todo campo, um `h1` por
  página, landmarks (`header`, `nav`, `main`, `footer`).
- Tudo funciona só com teclado, com foco visível (`:focus-visible`) e ordem lógica.
- Contraste mínimo 4.5:1 (texto) e 3:1 (componentes e texto grande). Cor nunca é o único sinal.
- Imagens informativas com `alt`; decorativas com `alt=""`. Ícone-botão com `aria-label`.
- Erros de formulário: mensagem textual ligada ao campo (`aria-describedby`), foco no primeiro erro.
- Conteúdo que muda sozinho (toast, resultado de busca): região `aria-live="polite"`.
- Respeite `prefers-reduced-motion`.

## Testar automaticamente (tests/e2e)

```python
from axe_playwright_python.sync_playwright import Axe

def test_home_sem_violacoes_graves(page, live_server):
    page.goto(live_server)
    results = Axe().run(page)
    graves = [v for v in results.response["violations"] if v["impact"] in ("serious", "critical")]
    assert not graves, results.generate_report()
```

Rode axe em cada tela principal e **também depois de interações** (modal aberto, formulário com
erro). axe pega ~40% dos problemas; complete com um teste de teclado por fluxo principal:
`page.keyboard.press("Tab")` até a ação e `Enter`, verificando o resultado.
