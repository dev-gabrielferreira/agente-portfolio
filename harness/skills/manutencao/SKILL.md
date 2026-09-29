---
name: manutencao
description: Como alterar com segurança um projeto que já está em produção (bug, incidente, nova feature, atualização). Use em qualquer tarefa de manutenção.
---

# Manutenção de projeto em produção

1. **Entenda antes de mexer.** Leia `.harness/PROJECT.md` (mapa do projeto), `SPEC.md`,
   `docs/DECISIONS.md` e o `git log` da área afetada.
2. **Bug ou incidente: reproduza primeiro.** Escreva um teste que falha pelo motivo do bug. Só
   então corrija. O teste fica como proteção de regressão. Diagnóstico sem reprodução é chute —
   registre a evidência que sustenta a causa em PROGRESS.md.
3. **Diff mínimo.** Mude só o necessário para a tarefa. Refatoração oportunista vira outra tarefa.
4. **Compatibilidade de dados.** Mudança de schema é aditiva (coluna nova com default). Nada que
   apague ou reescreva dados de `/data` sem migração reversível descrita em DECISIONS.md.
5. **Compatibilidade de API.** Não quebre rotas/contratos existentes; se precisar, versione.
6. **Toda a suíte continua verde**, não só os testes novos.
7. **Registre** em PROGRESS.md: sintoma, causa raiz, correção, como verificar em produção.

## Projeto adotado (que não nasceu do template)

Na primeira tarefa, gere `.harness/PROJECT.md` com: propósito, arquitetura, como rodar, como
testar, variáveis de ambiente, onde ficam os dados, riscos conhecidos. Depois adapte o projeto ao
contrato de deploy (Dockerfile, porta 8000, `/health`, dados em `/data`) **sem mudar
comportamento**, e crie testes mínimos que protejam os fluxos principais antes de qualquer outra
mudança.
