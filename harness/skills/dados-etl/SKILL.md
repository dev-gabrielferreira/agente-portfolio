---
name: dados-etl
description: Padrões de engenharia de dados para projetos de pipeline/ETL do portfólio (ingestão de APIs, Parquet, DuckDB, arquitetura medalhão, agendamento, idempotência). Use em projetos que coletam, transformam ou analisam dados.
---

# Pipelines de dados

- **Camadas medalhão** em `/data/lake/`: `bronze/` (bruto, como veio, particionado por data de
  ingestão), `silver/` (limpo, tipado, deduplicado), `gold/` (agregado para consumo). Parquet com
  compressão zstd; particione por data quando o volume justificar.
- **DuckDB** para transformar e consultar Parquet (SQL legível, rápido, sem servidor). Pandas/Polars
  só onde SQL ficaria pior.
- **Idempotência**: rodar o mesmo período duas vezes gera o mesmo resultado (sobrescreva a
  partição, não acrescente). Guarde um controle de execução (`/data/state.db`: fonte, período,
  status, linhas, duração).
- **Ingestão resiliente**: `httpx` com timeout, retry com backoff exponencial, respeito a rate
  limit, paginação explícita. Salve a resposta bruta antes de transformar.
- **Histórico grande**: backfill em lotes com checkpoint; nunca carregue tudo em memória.
- **Qualidade**: checagens explícitas por camada (nulos em chave, duplicatas, faixa de valores,
  contagem vs. período anterior); falha de qualidade interrompe a promoção para a próxima camada
  e aparece na interface.
- **Agendamento**: APScheduler no app com trava; expor `GET /api/pipeline/runs` e um botão de
  execução manual protegido.
- **Consumo**: API de leitura sobre a camada gold + um dashboard que mostra dados reais e o
  estado do pipeline (última execução, atraso, erros). Isso mostra maturidade de engenharia.
- Documente no README a linhagem (fonte → bronze → silver → gold) com um diagrama.
