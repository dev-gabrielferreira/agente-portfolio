# O VPS: quem mexe em quê e como conferir

O passo a passo de instalação, do zero ao celular, está em [`PRODUCAO.md`](../PRODUCAO.md). Este
guia é a referência: o que roda, o que o agente pode mudar no servidor, o que cada verificação do
`verificar-vps.sh` confere e quem paga cada credencial.

## O que vai rodar e quem mexe em quê

```
 VPS
 ├─ caddy (SEU container, já existe)   seu Caddyfile  ──import──▶  /srv/agente/caddy-sites/*.caddy
 │                                                                   (arquivos que o orquestrador escreve)
 ├─ agente   orquestrador (root, com o socket do Docker) + workers claude -p (usuário agent, SEM Docker)
 │           painel https://agent.gabrielfdev.com · API interna do Jarvis (só rede Docker)
 ├─ jarvis   sua central pelo app do Claude (usuário jarvis, SEM Docker, projetos só leitura):
 │           conversa "Jarvis" + uma conversa por projeto + laboratório (usuário lab)
 └─ <projeto>-staging / <projeto>-production   um container por projeto e ambiente, criados no deploy
```

| O quê | Onde fica | Quem muda | Como |
|---|---|---|---|
| Código de cada projeto do agente | `/srv/agente/projects/<projeto>` (git) | o builder, dentro do pipeline | tickets, gates, testes, sua aprovação |
| Containers dos projetos | `<projeto>-staging`, `<projeto>-production` | o orquestrador | deploy, rollback automático se o health falhar |
| Compose de cada ambiente | `/srv/agente/deploy/<container>/compose.yml` | o orquestrador | gerado a cada deploy (não edite à mão) |
| Site de cada projeto no Caddy | `/srv/agente/caddy-sites/<container>.caddy` | o orquestrador | escreve o bloco, roda `caddy reload`; se o reload falhar, desfaz o arquivo |
| Variáveis e segredos de projeto | `/srv/agente/secrets/<projeto>.<ambiente>.env` | você, pelo painel | só o root lê |
| Dados persistentes do projeto | volume Docker `<container>-data` (montado em `/data`) | a aplicação | sobrevive a deploy e rollback |
| **Seu Caddyfile principal** | na pasta do seu Caddy | **você**, por proposta do Jarvis | o Jarvis propõe; o orquestrador aplica só com o seu código do autenticador + a confirmação da proposta (ou pelo painel → Infra), com backup e volta automática se o Caddy recusar ou um site parar |
| Backups do Caddyfile principal | `/srv/agente/infra/` | o orquestrador | um arquivo antes de cada mudança aplicada |
| **Outros sites e pastas do VPS** | fora de `/srv/agente` | **você** | o agente não enxerga |

Resumindo: o agente cria, atualiza e remove sozinho os sites e containers **dos projetos dele**,
incluindo a parte do Caddy (domínio, HTTPS, proxy, senha do staging). No **seu Caddyfile principal**
ele só **propõe**: nada é gravado sem o seu código e a confirmação daquela proposta, e as regras
impedem tirar o `import` dos sites do agente, a proteção da API ou expor o `admin` do Caddy. Pastas de
outros projetos do VPS ele não enxerga.

Para o agente passar a cuidar de um projeto que você já tem no ar, **adote** o projeto (painel →
Adotar projeto, ou `/adotar <repositório>` no Jarvis). Ele clona, testa e sobe no staging. Antes do
deploy de produção, o bloco antigo desse domínio precisa sair do seu Caddyfile (o mesmo domínio em
dois lugares faz o Caddy recusar a configuração): peça `/caddy tira o bloco antigo do <domínio>`,
aprove, e logo em seguida `/aprovar deploy`.

## Confirmando: `verificar-vps.sh`

```bash
cd /opt/agente-portfolio
sudo ./scripts/verificar-vps.sh            # completo (faz 1 chamada mínima ao Opus pela assinatura)
sudo ./scripts/verificar-vps.sh --rapido   # sem chamar o modelo
```

Ele só lê e testa, não muda nada. Rode depois da instalação, depois de cada atualização
(`git pull && docker compose build && docker compose up -d`) e sempre que algo parecer estranho.
Cada linha sai com ✔ (ok), ! (aviso: funciona, mas vale olhar) ou ✖ (falha, com a correção na
própria linha). As cinco partes:

1. **Base do VPS**: Docker, `.env` com permissão 600, token da assinatura, `ANTHROPIC_API_KEY`
   vazia (se estiver preenchida, avisa que seria cobrado por uso), modelo Opus, token do GitHub,
   token da API do Jarvis e TOTP, redes, pastas, memória e disco.
2. **Orquestrador**: container no ar, painel respondendo, usuário `agent` **sem** Docker, versão
   do Claude Code e uma chamada real ao Opus pelo token da assinatura.
3. **Caddy e domínios**: Caddy nas redes certas, montando a pasta dos sites, importando os sites
   do agente, e o orquestrador conseguindo validar a configuração através do Caddy (é o mesmo
   caminho do `caddy reload` do deploy). Confere o painel por HTTPS, que a API do Jarvis está
   fechada para a internet (404) e o DNS curinga. Lista os sites do seu Caddyfile que continuam
   com você, acusa domínio repetido entre o seu Caddyfile e os sites do agente e avisa se o
   Caddyfile está só leitura (aí as propostas do Jarvis não podem ser aplicadas).
4. **Jarvis**: container no ar, sem variáveis que desligam o Remote Control, login **completo** da
   assinatura, sessão rodando e com o Remote Control ativo (ou esperando o seu `y`), broker do
   laboratório, conversa com o orquestrador pelo token, e o isolamento: sem Docker, projetos só
   leitura, laboratório sem acesso aos arquivos do Jarvis. Lista as conversas por projeto e acusa a
   que estiver esperando o `y` do Remote Control. Mostra se o Laboratório pelo app está ligado.
5. **Projetos**: cada container de projeto e o `/health` de cada site por HTTPS.

Falhas mais comuns e o que fazer:

| ✖ | Correção |
|---|---|
| `Caddy não monta /srv/agente/caddy-sites` ou `Caddy fora da rede agente-painel` | compose do Caddy (PRODUCAO.md, passo 4.2), `docker compose up -d` na pasta dele |
| `seu Caddyfile não tem 'import …'` | acrescente a linha do `deploy/Caddyfile.snippet` e recarregue o Caddy |
| `caddy validate falhou` | rode o comando que a linha mostra: o erro do Caddy diz o arquivo e a linha |
| `domínio X está no seu Caddyfile E nos sites do agente` | apague o bloco antigo desse domínio do seu Caddyfile e recarregue |
| `a API respondeu '200' pela internet` | o bloco do painel precisa do `@api path /api/*` + `respond @api 404` do snippet |
| `worker não conseguiu chamar o modelo` | token do `.env` errado ou expirado: `claude setup-token` de novo no notebook, cole no `.env`, `docker compose up -d` |
| `Jarvis sem login` / `exige o login completo` | `sudo ./scripts/setup-jarvis.sh` (login com `claude auth login`, não com token) |
| `a sessão espera você responder 'Enable Remote Control?'` | `docker exec -it -u jarvis jarvis tmux attach -t jarvis`, responda `y`, saia com `Ctrl+b` `d` |
| `API interna respondeu '401' ao Jarvis` | o `JARVIS_API_TOKEN` mudou no `.env`: `docker compose up -d` recria os dois containers com o mesmo valor |
| `a conversa do projeto X espera o 'Enable Remote Control?'` | `docker exec -it -u jarvis jarvis tmux attach -t projetos:X`, responda `y`, saia com `Ctrl+b` `d` |
| ! `Caddyfile principal só leitura` | tire o `:ro` do volume do Caddyfile no compose do Caddy (PRODUCAO.md, passo 4.2), se quiser que as propostas possam ser aplicadas |

## Credenciais: o que é cada uma e quem paga

| Credencial | Para que serve | Gasta o quê |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) | workers do pipeline e sessões em segundo plano do laboratório chamam o Claude | **sua assinatura** (limite de uso do plano) |
| login completo do Jarvis (`claude auth login`) | sessão Jarvis + Remote Control (o app do celular) | **sua assinatura** |
| `JARVIS_API_TOKEN` | senha aleatória entre o Jarvis e o orquestrador (`http://agente:8080/api/v1`), só na rede Docker | **nada**: não é token da Anthropic, não chama modelo |
| `ADMIN_TOTP_SECRET` | segundo fator: o código de 6 dígitos que libera deploy, rollback e mudanças no Caddyfile | nada |
| `ANTHROPIC_API_KEY` | **deixe vazia.** Se preenchida, tem prioridade e é cobrada por uso | dinheiro por token |

Tudo o que chama o Claude usa o **Opus** (`AGENT_MODEL` e `JARVIS_MODEL` = `claude-opus-5-5`) e
divide o mesmo limite da assinatura. Quando o limite acaba, o job espera e volta sozinho.

## Rotina

| Quando | Comando |
|---|---|
| Atualizar o agente | `cd /opt/agente-portfolio && git pull && docker compose build && docker compose up -d && sudo ./scripts/verificar-vps.sh --rapido` |
| Algo estranho | `sudo ./scripts/verificar-vps.sh`, depois `docker logs -f agente` ou `docker logs -f jarvis` |
| Ver a sessão do Jarvis no terminal | `docker exec -it -u jarvis jarvis tmux attach -t jarvis` (sair: `Ctrl+b` `d`) |
| Projetos rodando | `docker ps --filter label=agente.project` |
| Backup diário | cron: `15 3 * * * /opt/agente-portfolio/scripts/backup.sh /var/backups/agente` |
