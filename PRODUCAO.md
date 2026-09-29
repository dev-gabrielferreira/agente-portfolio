# Colocando o agente e o Jarvis em produção no VPS

Roteiro do zero até o Jarvis no celular, na ordem, com os comandos para copiar. Na primeira vez
leva cerca de uma hora (a maior parte é esperar build e DNS). Onde aparece `gabrielfdev.com`, use o
domínio do `.env` (`PORTFOLIO_DOMAIN`).

## O que vai rodar

```
VPS (Ubuntu 24.04, Docker)
├─ caddy       SEU container (já existe): HTTPS de tudo
├─ agente      orquestrador + painel (agent.gabrielfdev.com) + workers do Claude Code
├─ jarvis      a central do celular: conversa "Jarvis" + uma conversa por projeto + laboratório
└─ <projeto>-staging / <projeto>-production   criados pelo agente a cada deploy
```

Tudo o que chama o Claude usa a **sua assinatura** e o **Opus**. Nada é cobrado por uso enquanto
`ANTHROPIC_API_KEY` ficar vazia.

## Antes de começar (checklist)

- [ ] VPS com Ubuntu 24.04, Docker e o Caddy rodando em container; 4 GB de RAM livres (ou swap).
- [ ] Plano Max (Pro funciona, mas o limite acaba rápido com Opus).
- [ ] Notebook com Node e navegador (para gerar o token da assinatura).
- [ ] App do Claude no celular, logado na mesma conta.
- [ ] App autenticador no celular (Google Authenticator, Aegis ou 1Password).
- [ ] (Recomendado) app **ntfy** no celular: é por ele que chegam os avisos e a confirmação das
      mudanças no Caddyfile.

---

## 1. No seu notebook

### 1.1 Token da assinatura para os workers

```bash
npm install -g @anthropic-ai/claude-code
claude setup-token
```

Entre com a conta da assinatura e guarde o token (`sk-ant-oat…`). Ele vai no `.env` do VPS.

### 1.2 Token do GitHub

GitHub → Settings → Developer settings → **Fine-grained tokens** → "All repositories" da conta
`dev-gabrielferreira`, com **Contents: Read and write** e **Administration: Read and write**.
Validade de 1 ano (anote para renovar).

### 1.3 Código num repositório privado

```bash
unzip agente-portfolio.zip && cd agente-portfolio
git init -b main && git add -A && git commit -m "agente de portfólio"
# com o GitHub CLI:
gh repo create dev-gabrielferreira/agente-portfolio --private --source . --push
# ou crie o repositório privado no site e depois:
# git remote add origin https://github.com/dev-gabrielferreira/agente-portfolio.git && git push -u origin main
```

## 2. DNS

No painel do seu domínio, crie dois registros apontando para o IP do VPS:

| Tipo | Nome | Valor |
|---|---|---|
| A | `agent` | IP do VPS |
| A | `*` | IP do VPS (cada projeto ganha `<projeto>.` e `<projeto>-staging.` sozinho) |

Confira depois de alguns minutos: `getent hosts qualquer-coisa.gabrielfdev.com` deve mostrar o IP.

## 3. No VPS: instalar o agente

```bash
sudo git clone https://github.com/dev-gabrielferreira/agente-portfolio /opt/agente-portfolio
# usuário: dev-gabrielferreira · senha: o token do passo 1.2
cd /opt/agente-portfolio
sudo cp .env.example .env && sudo chmod 600 .env
sudo nano .env
```

Preencha no `.env`:

| Variável | O que colocar |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | o token do passo 1.1 |
| `ANTHROPIC_API_KEY` | **vazio** |
| `GITHUB_TOKEN` | o token do passo 1.2 |
| `GIT_AUTHOR_EMAIL` | seu e-mail do GitHub |
| `NOTIFY_URL` | `https://ntfy.sh/<um-tópico-difícil-de-adivinhar>` (assine o mesmo tópico no app ntfy) |
| `CADDY_CONTAINER` / `CADDY_CONFIG_PATH` | nome do seu container do Caddy e caminho do Caddyfile **dentro** dele (padrão `caddy` e `/etc/caddy/Caddyfile`) |

O resto já vem com valores bons (modelo Opus, limites, conversas por projeto). Depois:

```bash
sudo ./scripts/install-vps.sh
```

Ele cria `/srv/agente`, as redes Docker, a senha do painel (mostra na tela; fica no `.env`), faz o
build e confere que o usuário que escreve código **não** acessa o Docker.

## 4. Caddy

### 4.1 Caddyfile

Acrescente ao seu Caddyfile o conteúdo de [`deploy/Caddyfile.snippet`](deploy/Caddyfile.snippet):
o bloco do painel (com a proteção `respond @api 404`, que fecha a API do Jarvis para a internet) e a
linha `import /etc/caddy/sites-agente/*.caddy`.

### 4.2 Compose do Caddy

No `docker-compose.yml` do seu Caddy, garanta estes três pontos:

```yaml
services:
  caddy:
    image: caddy:2
    container_name: caddy
    restart: unless-stopped
    ports: ["80:80", "443:443", "443:443/udp"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile                    # SEM ":ro": o agente aplica as mudanças que você aprovar
      - /srv/agente/caddy-sites:/etc/caddy/sites-agente:ro   # sites que o agente cria para cada projeto
      - caddy_data:/data
      - caddy_config:/config
    networks: [interna, agente-painel]
networks:
  interna: { external: true }
  agente-painel: { external: true }
volumes:
  caddy_data: {}
  caddy_config: {}
```

Se preferir que o agente só **proponha** e nunca grave o seu Caddyfile, mantenha o `:ro`: as
propostas continuam chegando, e você aplica à mão.

```bash
cd /caminho/do/seu/caddy
docker compose up -d
docker exec caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
```

Abra `https://agent.gabrielfdev.com`: deve aparecer o login do painel.

## 5. Jarvis

```bash
cd /opt/agente-portfolio
sudo ./scripts/setup-jarvis.sh
```

O script, na ordem:

1. Gera o token da API interna do Jarvis (uma senha entre os dois containers; não gasta nada).
2. Mostra o **QR do segundo fator**: escaneie no app autenticador. É o código de 6 dígitos que
   libera deploy, rollback e mudanças no Caddyfile.
3. Sobe os containers `agente` e `jarvis`.
4. Faz o login **completo** da sua assinatura no Jarvis (`claude auth login`): abra o link, entre,
   cole o código. O Remote Control exige esse login; o token do passo 1.1 não serve para isso.
5. Pergunta se você quer o login do **Laboratório** (opcional: abre conversas livres no VPS pelo app).
6. Abre a sessão do Jarvis no terminal. Lá: confirme a pasta, responda `y` em
   **Enable Remote Control?**, digite `/config` e ligue **Push when actions required** e **Push
   when Claude decides**. Saia com `Ctrl+b` e depois `d` (a sessão continua rodando).
7. Se alguma conversa de projeto pedir a mesma confirmação, ele abre essa também.

## 6. Conferir tudo

```bash
sudo ./scripts/verificar-vps.sh
```

Confere a base, o orquestrador (com uma chamada real ao Opus pela assinatura), o Caddy e os
domínios, o Jarvis (login, Remote Control, conversas por projeto, isolamento) e os projetos no ar.
Tem que terminar com `0 falha(s)`. Cada falha já vem com a correção; a tabela completa está em
[`docs/VPS.md`](docs/VPS.md).

## 7. No celular

App do Claude → **Code** → **Jarvis** (computador com bolinha verde). Teste:

- `/ajuda` → a lista de comandos.
- `/status` → a situação geral.

## 8. Primeiro projeto (pequeno, para ver o ciclo inteiro)

Na conversa **Jarvis**:

1. `/novo Uma página que converte temperaturas entre °C, °F e K, com histórico das últimas conversões`
2. Chegam as perguntas numeradas, cada uma com a recomendação. Responda: `/responder 1 ok, 2 ok, 3 só eu`.
3. Em cerca de um minuto aparece no app a conversa **Jarvis · Conversor de Temperaturas**. Dali em
   diante, fale do projeto lá.
4. Spec e plano prontos: `/plano` (resumo, arquitetura e tickets detalhados) → `/aprovar spec`, ou
   `/ajustar <o que mudar>`.
5. Design (se tiver interface): veja os mockups pelo link do painel → `/aprovar design`.
6. O agente constrói um ticket por sessão, testa, sobe no staging, avalia e revisa. `/status` ou
   `/logs` quando quiser.
7. Staging no ar: abra `https://<projeto>-staging.gabrielfdev.com` e confira. Depois
   `/aprovar deploy 123456` (o código do autenticador).
8. Produção em `https://<projeto>.gabrielfdev.com` e repositório público no GitHub.

## 9. Conversas por projeto

- Cada projeto aparece no app como **Jarvis · <Projeto>**, com as mesmas regras, comandos, skills
  e ferramentas da conversa central. Nela, comando sem argumento vale para o projeto (`/status`,
  `/tickets`, `/mudar …`, `/aprovar deploy …`).
- Ficam no ar os `JARVIS_CONVERSAS_MAX` (padrão 6) projetos mais recentes, mais os de
  `JARVIS_CONVERSAS_FIXAS` (slugs separados por vírgula). Cada conversa aberta usa ~300–500 MB; o
  container do Jarvis tem limite de `JARVIS_MEM_LIMIT` (6 GB).
- O histórico de cada conversa volta sozinho quando o container reinicia. Projeto que sai da lista
  dos mais recentes perde a janela, mas a nota dele continua na base de conhecimento; ao voltar a
  ter atividade, a conversa reaparece.
- Para desligar: `JARVIS_CONVERSAS=0` no `.env` e `docker compose up -d jarvis`.

## 10. Mudanças no seu Caddyfile pelo Jarvis

Exemplo: `/caddy redireciona www.gabrielfdev.com para gabrielfdev.com`.

1. O Jarvis lê o Caddyfile (segredos aparecem para ele como «segredo-N»), propõe a edição e mostra o
   diff e os alertas.
2. O orquestrador confere as regras (não deixa tirar o `import` dos sites do agente, nem a proteção
   da API, nem expor o `admin` do Caddy, nem mover um segredo para onde o visitante veria) e valida
   no próprio Caddy.
3. Chega no celular (ntfy) o push **"Caddyfile: proposta #N"**, com o resumo calculado pelo
   orquestrador (sites que entram e saem) e a **confirmação**, um código de 6 caracteres.
4. Para aplicar, mande ao Jarvis os dois: `/caddy aprovar N 123456 K7Q2MX`. Ou abra o painel →
   **Infra**, veja o diff real e aplique com o código do autenticador.
5. Na hora ele faz backup, recarrega o Caddy e mede os sites: se o reload falhar ou um site que
   respondia parar, volta o arquivo anterior sozinho. Desfazer depois: `/caddy desfazer N <código>
   <confirmação>`, ou o botão no painel.

A confirmação existe porque o Jarvis lê páginas da internet e pode ser enganado: sem ela, um Jarvis
manipulado poderia mostrar uma proposta e aplicar outra com o seu código.

## 11. Rotina

| Quando | Comando (em `/opt/agente-portfolio`) |
|---|---|
| Atualizar o agente | `git pull && docker compose build && docker compose up -d && sudo ./scripts/verificar-vps.sh --rapido` |
| Algo estranho | `sudo ./scripts/verificar-vps.sh`, depois `docker logs -f agente` ou `docker logs -f jarvis` |
| Ver o Jarvis no terminal | `docker exec -it -u jarvis jarvis tmux attach -t jarvis` (sair: `Ctrl+b` `d`) |
| Ver as conversas por projeto | `docker exec -it -u jarvis jarvis tmux attach -t projetos` (`Ctrl+b` `w` lista) |
| Backup diário | `sudo crontab -e` → `15 3 * * * /opt/agente-portfolio/scripts/backup.sh /var/backups/agente` |
| Renovar o token da assinatura | `claude setup-token` no notebook → `.env` → `docker compose up -d` |

## 12. Se algo der errado

| Sintoma | O que fazer |
|---|---|
| "Jarvis" não aparece no app | `sudo ./scripts/setup-jarvis.sh` de novo (login completo e o `y` do Remote Control) |
| Conversa de projeto não aparece | espere 1 minuto; `docker logs jarvis \| grep conversa`; confira `JARVIS_CONVERSAS=1` |
| Comando `/…` não aparece no app | atualize o app; confira que o container subiu depois do `git pull` (`docker compose up -d jarvis`) |
| Deploy recusado com o código certo | relógio do celular fora de hora ou código já usado: espere o próximo |
| Proposta do Caddy "obsoleta" | alguém mudou o Caddyfile depois da proposta: peça uma nova (`/caddy …`) |
| Proposta do Caddy "revertida" | o Caddy recusou ou um site parou: leia o motivo no painel (Infra) e peça outra |
| "limite de uso atingido" | normal na assinatura; o job volta sozinho depois de `RATE_LIMIT_BACKOFF_S` |

Referências: [`README.md`](README.md) (visão geral), [`docs/JARVIS.md`](docs/JARVIS.md) (o Jarvis
em detalhe), [`docs/VPS.md`](docs/VPS.md) (o que o agente pode mudar no servidor e cada verificação).
