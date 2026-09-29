---
name: integracao-llm
description: Como integrar LLMs nos projetos (cliente compatível com OpenAI, modelos locais ou em nuvem, RAG com embeddings locais, custos e segurança). Use quando a spec tiver funcionalidade de IA.
---

# Integração com LLM

Padrão do Gabriel: API-first, **cliente compatível com a API da OpenAI** com `base_url`
configurável, para trocar entre modelo local e nuvem só por variável de ambiente.

```python
# app/llm.py
from openai import AsyncOpenAI
client = AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
```

Variáveis: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_MAX_TOKENS`, `LLM_TIMEOUT_S`.
Para Claude direto, use o SDK `anthropic` atrás da mesma interface `LLMClient` (um Protocol) —
o resto do código não sabe qual provedor está por trás.

- **RAG**: embeddings locais em CPU (ex.: `fastembed` ou `sentence-transformers` pequeno),
  índice em SQLite (`sqlite-vec`) ou DuckDB; guarde chunk, fonte e posição para citar.
- **Ferramentas/agentes**: se o LLM aciona funções do app, cada ferramenta valida entrada e
  nenhuma ação destrutiva roda sem confirmação do usuário.
- **Custo e abuso**: limite de requisições por IP, tamanho máximo de entrada, cache de respostas
  idênticas, timeout. Registre tokens usados por requisição.
- **Prompt injection**: conteúdo do usuário e documentos recuperados vão delimitados como dados,
  nunca concatenados às instruções do sistema.
- **Testes**: um `FakeLLMClient` determinístico; nenhum teste chama modelo real.
- **Sem chave configurada**, a funcionalidade degrada com mensagem clara — o resto do app funciona.
