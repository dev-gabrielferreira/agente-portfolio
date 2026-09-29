---
name: novo-projeto
description: "Conduz um pedido de projeto novo pelo celular — da ideia do Gabriel ao job no pipeline, passando pelas rodadas de perguntas (sabatina) e pela aprovação da spec e do plano. Use quando ele quiser criar, prototipar ou \"fazer um app/site/API/sistema\"."
user-invocable: false
---

# Projeto novo, do celular à spec aprovada

O objetivo é que o pipeline receba um pedido **sem ambiguidade**: a causa nº 1 de retrabalho é o
modelo preencher lacunas sozinho. Você é o entrevistador; o planner do pipeline é o especialista.

## 1. Entenda o pedido (antes de abrir o job)

Confirme em uma mensagem curta o que você entendeu e pergunte **só o que falta** destes pontos:

- objetivo (que problema resolve, para quem) e o que seria sucesso;
- o que precisa ter na primeira versão (e o que fica de fora);
- dados/integrações (APIs, arquivos, login?), se é público no portfólio;
- referências visuais ou produtos parecidos (se tiver interface);
- prazo ou restrição (custo zero, sem banco externo…).

Não pergunte stack ou arquitetura: isso é do planner, que decide com ADRs. Se ele já disse tudo,
não pergunte nada — abra o job.

## 2. Abra o job

`novo_projeto` com o pedido **completo nas palavras dele + o que você esclareceu** (não resuma
demais: detalhes viram requisitos). Diga o número do job e que as perguntas do planner chegam em
alguns minutos.

## 3. Rodadas de perguntas do planner

Quando o job estiver esperando `answers` (`job` mostra as perguntas):

1. Mostre as perguntas numeradas, cada uma com a **recomendação** do planner entre parênteses e o
   porquê em poucas palavras. Agrupe por tema se forem muitas.
2. Aceite respostas curtas: "1 ok, 2 não, 3: só PIX". "Pode seguir com as recomendações" vale para
   todas as que ele não comentou.
3. Monte o objeto `{id: resposta}` com exatamente o que ele decidiu e chame `responder_perguntas`
   (a confirmação aparece no celular dele — mostre antes o que vai enviar).
4. Pode haver mais de uma rodada: o planner pergunta de novo quando uma resposta abre decisões
   novas. Repita o ciclo.

## 4. Spec e plano

Quando esperar `spec_approval`: resuma em até 8 linhas (o que será construído, arquitetura
escolhida e por quê, o que ficou fora, riscos). Ofereça o texto completo. Ele decide:

- "aprovado" → `aprovar` etapa `spec`;
- ajustes → `pedir_ajustes` com os pontos **específicos e verificáveis** que ele pediu.

Depois disso o pipeline segue sozinho até precisar dele de novo (design, deploy). Veja as skills
`acompanhar` e `aprovacoes`.
