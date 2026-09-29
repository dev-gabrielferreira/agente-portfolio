---
name: manutencao
description: "Mudanças, correções e melhorias em projetos que já estão no ar — investigar o código, escrever um pedido preciso e abrir o job de mudança ou incidente. Use quando o Gabriel quiser alterar, corrigir, evoluir ou entender um projeto existente."
user-invocable: false
---

# Manutenção de projetos em produção

1. **Contexto primeiro**: `nota_do_projeto` (arquitetura, decisões, pendências). Só então o código
   em `/srv/projetos/<slug>` (somente leitura) — ou delegue a leitura pesada a um subagente
   `analista-de-projeto` / sessão com `projeto` anexado, para não encher esta conversa.
2. **Esclareça** o que ele quer em termos observáveis: o que acontece hoje, o que deveria acontecer,
   como saber que ficou certo. Uma pergunta com recomendação por vez.
3. **Escreva o pedido** para `mudar_projeto`:
   - o problema/objetivo em 1–2 frases;
   - critérios de aceite verificáveis (entrada → resultado esperado);
   - o que NÃO mudar (escopo);
   - pistas que você encontrou no código (arquivos, funções), marcadas como pistas.
4. Tipo `incidente` só para algo quebrado em produção; o resto é `mudanca`.
5. Acompanhe com a skill `acompanhar`; o deploy volta para ele aprovar (skill `aprovacoes`).

## Projeto que ainda não é do agente (adoção)

Se o Gabriel quer que o agente passe a cuidar de um projeto que já existe (no GitHub, às vezes já no
ar no VPS com um bloco próprio no Caddyfile dele), use `adotar_projeto` com a URL do repositório e
instruções: domínio atual, variáveis de ambiente necessárias, o que não pode mudar. O agente clona,
escreve testes de caracterização e sobe no staging. **Antes de ele aprovar a produção**, lembre-o de
apagar ou comentar o bloco antigo desse domínio no Caddyfile principal (o agente não edita esse
arquivo; o mesmo domínio em dois lugares faz o Caddy recusar a configuração).

Nunca edite o projeto diretamente, nem "só um ajuste pequeno": o caminho seguro é o pipeline.
