---
name: seguranca-web
description: Segurança de aplicações web em FastAPI (OWASP Top 10) — entrada, injeção, XSS, autenticação, sessões, CORS, uploads, segredos, cabeçalhos e dependências. Use ao escrever qualquer código exposto à internet e ao revisar mudanças antes da produção.
---

# Segurança web (o que vale para um VPS público)

- **Entrada**: todo dado externo passa por schema Pydantic com limites (tamanho, faixa, enum).
- **SQL**: só consultas parametrizadas/ORM. Nunca f-string com valor do usuário, nem em `ORDER BY`
  (use lista branca).
- **Comandos**: evite `subprocess`; se inevitável, lista de argumentos e `shell=False`.
- **Arquivos**: upload com limite de tamanho, tipo verificado pelo conteúdo, nome gerado pelo
  servidor, salvo em `/data/uploads`; download resolve o caminho e confere que continua dentro da
  pasta (path traversal).
- **XSS**: Jinja2 com autoescape (padrão); nunca `|safe` em conteúdo do usuário. No front,
  `textContent` em vez de `innerHTML`.
- **Autenticação** (se a spec pedir): senha com `argon2`/`bcrypt`; sessão em cookie `HttpOnly`,
  `Secure`, `SameSite=Lax`; rate limit no login; mensagens que não revelam se o usuário existe.
- **Autorização**: verifique dono/perfil em **toda** rota que lê ou altera dado de alguém (IDOR).
- **CORS**: origem explícita; nunca `*` com credenciais.
- **Cabeçalhos**: `X-Content-Type-Options: nosniff`, `Referrer-Policy`, CSP básica quando houver
  front. O Caddy cuida de HTTPS/HSTS.
- **Erros**: nada de stack trace ou SQL na resposta; `debug=False` fora de dev.
- **Segredos**: só por variável de ambiente; nunca em código, teste, log ou commit.
- **Custos**: rotas que chamam LLM ou API paga têm rate limit e limite de tamanho de entrada.
- **Dependências**: versões mínimas no `pyproject.toml`; o gate roda `pip-audit`.
- **SSRF**: se o app busca URLs informadas pelo usuário, bloqueie IPs privados/metadados.
