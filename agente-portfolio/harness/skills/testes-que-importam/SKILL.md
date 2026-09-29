---
name: testes-que-importam
description: Método do test-engineer para escrever testes que encontram bugs de verdade (aceitação por comportamento, bordas, persistência, erros, segurança, contrato, propriedades, e2e e acessibilidade) e evitar testes feitos só para passar. Use em qualquer tarefa de teste de aceitação.
---

# Testes que importam

Um teste só tem valor se **falharia quando o software estivesse errado de um jeito que importa para
o usuário**. Teste que passa com qualquer implementação é custo sem proteção. Você mede isso com
mutation testing (skill `mutation-testing-mutmut`) — não com cobertura.

## 1. De onde vêm os testes

Da **SPEC e dos critérios de aceite**, nunca da implementação. Leia o código só para saber *como
chamar* (rotas, payloads, seletores acessíveis) — não para decidir *o que esperar*. O valor esperado
vem da regra de negócio escrita, calculado à mão ou por um oráculo independente.

Para cada feature de `.harness/features.json`, cubra **cada critério de aceite** com pelo menos um
teste marcado com o id:

```python
@pytest.mark.feature("F03")
def test_excluir_item_some_da_lista_e_nao_volta_apos_reload(client): ...
```

## 2. Onde procurar bugs (em ordem de retorno)

1. **Fluxo principal de ponta a ponta** — o usuário consegue fazer a coisa pela qual o produto
   existe? Um teste e2e por jornada principal (`tests/e2e/`).
2. **Persistência** — depois de criar/editar/excluir, um GET novo (ou reload da página) mostra o
   estado certo? Sobrevive a reiniciar o app (dados em `DATA_DIR`)?
3. **Validação e erros** — entrada vazia, tipo errado, fora da faixa, texto enorme, unicode/acentos,
   id inexistente (404), duplicado (409/422). Erro nunca vira 500 nem vaza stack trace.
4. **Limites** — 0, 1, máximo, máximo+1, lista vazia, paginação na última página, datas em
   virada de mês/ano/fuso (America/Sao_Paulo).
5. **Autorização** (se houver) — usuário A não lê nem altera dado de B; rota admin sem login → 401/403.
6. **Idempotência e concorrência** — repetir a mesma operação (duplo clique, retry, reprocessar o
   mesmo período no ETL) não duplica efeito.
7. **Integrações** — API externa lenta, fora do ar, resposta malformada: o app degrada com mensagem
   clara (use `respx`/fakes; nunca rede real).
8. **Dados (ETL)** — contagem de linhas bronze→silver→gold coerente, sem duplicata na chave,
   valores em faixa física plausível, reprocessamento idempotente.
9. **LLM** — com `FakeLLMClient`: prompt injection no conteúdo do usuário não vira instrução;
   sem chave configurada a feature degrada sem derrubar o resto.

## 3. Técnicas que multiplicam o alcance

- **Contrato/fuzz da API** — `tests/test_api_contract.py` (do template) usa Schemathesis para gerar
  milhares de requisições a partir do OpenAPI e falha em qualquer 500. Ele roda sozinho; seu papel
  é investigar o que ele encontrar.
- **Property-based** (Hypothesis, `tests/properties/`) para regra de negócio pura: invariantes
  ("total com desconto nunca excede o total", "ida e volta de serialização preserva o objeto",
  "agregação por dia soma igual ao total"). Uma propriedade vale por centenas de exemplos.
- **Parametrização por tabela** para regras com faixas: um caso por fronteira, com o valor esperado
  calculado à mão na tabela.
- **E2E com Playwright** (`tests/e2e/`, fixtures `live_server` e `page`): selecione por papel e texto
  acessível (`page.get_by_role("button", name="Salvar")`), não por classe CSS. Rode axe em cada tela
  principal (skill `acessibilidade`).

## 4. Anti-padrões — testes feitos para passar (o gate reprova vários)

| Anti-padrão | Por que é inútil | Faça assim |
|---|---|---|
| `assert resp.status_code == 200` sozinho | uma rota que devolve lixo passa | verifique o corpo e o efeito persistido |
| `assert x is not None` / `isinstance` sozinho | qualquer valor passa | afirme o valor esperado |
| esperado calculado com o próprio código | o bug aparece dos dois lados | valor à mão ou oráculo independente |
| mock da unidade testada ou do banco em teste de aceitação | testa o mock | banco real temporário, fake só na borda externa |
| snapshot do que a função devolve hoje | congela bugs | afirme a regra |
| `skip`/`xfail` para esconder falha | esconde bug | reporte o bug |
| `sleep` para esperar | lento e intermitente | espere por condição (`expect(...).to_be_visible()`) |
| teste que só percorre o caminho feliz | o bug mora nas bordas | seção 2, itens 3–7 |
| um teste gigante verificando tudo | falha sem dizer o quê | um comportamento por teste, nome que explica |

## 5. Quando um teste seu falhar

Primeiro confirme que o **teste** está certo (releia a SPEC). Se estiver, **é um bug**: não mude o
teste, não o marque como skip. Registre no relatório (`bugs`) com: feature, teste, observado,
esperado. O builder corrige o código.

## 6. Disputas

Se `.harness/TEST_DISPUTES.md` existir, julgue cada item contra a SPEC:
- o builder tem razão → corrija o teste e explique;
- o teste está certo → mantenha e responda no próprio arquivo com a evidência.
Nunca afrouxe um teste para encerrar a discussão.
