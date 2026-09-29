---
name: banco-de-dados
description: Modelagem e acesso a dados com SQLAlchemy 2.0 e SQLite/Postgres — schema, índices, migrações com Alembic, transações, N+1 e dados em /data. Use ao criar ou mudar modelos, consultas ou migrações.
---

# Banco de dados

- **SQLAlchemy 2.0** tipado (`Mapped[int]`, `mapped_column`), sessão por requisição via dependência.
- **SQLite em `/data/app.db`** por padrão, com `PRAGMA journal_mode=WAL` e `foreign_keys=ON`
  (evento `connect`). Postgres só se a spec justificar (concorrência de escrita alta, recursos
  específicos).
- **Chaves e restrições no banco**, não só no código: `UNIQUE`, `NOT NULL`, `FOREIGN KEY`,
  `CHECK` para faixas. O banco é a última linha de defesa contra dado inválido.
- **Índices** para toda coluna usada em filtro, junção ou ordenação frequente. Consulte o plano
  (`EXPLAIN QUERY PLAN`) quando uma listagem ficar lenta.
- **N+1**: listagens com relacionamento usam `selectinload`/`joinedload`. Um teste pode contar
  consultas com evento `before_cursor_execute`.
- **Transações**: uma unidade de trabalho por operação de negócio; nada de commit no meio de um
  laço. Operações em lote usam `executemany`/`insert().values([...])`.
- **Migrações com Alembic** a partir do primeiro deploy em produção. Toda migração é **aditiva e
  reversível** (coluna nova com default; renomear = adicionar + copiar + remover em deploys
  separados). Rode `alembic upgrade head` no startup com trava. Nunca apague dados de `/data`.
- **Datas** em UTC no banco; converta para America/Sao_Paulo só na apresentação.
- **Dinheiro** em inteiro de centavos ou `Decimal`, nunca `float`.
- **Seeds** de demonstração (para o portfólio ter conteúdo) ficam em comando separado e idempotente,
  nunca misturados com migração.
