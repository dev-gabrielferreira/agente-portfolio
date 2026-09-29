# Lições aprendidas

Regras que surgiram de problemas reais em jobs anteriores. Cada uma foi aprovada pelo Gabriel no
painel. Mais recentes no fim. Quando uma lição vira teste, lint ou hook, ela sai daqui.

- Em FastAPI, declare rotas estáticas (ex.: `/items/reorder`) **antes** das rotas com parâmetro
  (`/items/{item_id}`); senão o parâmetro captura o caminho e a API responde 422.
- Botão ou tela que só exibe dados sem a interação prometida na spec conta como feature não
  entregue. "Stub" nunca é `passes: true`.
