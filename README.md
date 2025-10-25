# Cerebroso — Lembretes e Pomodoro

Este é o post informativo oficial do servidor sobre o bot **Cerebroso — Lembretes e Pomodoro**. Compartilhe com todo mundo! Ele explica como participar das rotinas, usar o pomodoro em grupo, criar lembretes pessoais e configurar todas as ferramentas da comunidade.

---

## 💡 Visão geral
O Cerebroso combina quatro sistemas principais, todos em português e acessíveis por comandos *slash*:

1. **Pomodoro de canal** – sessões focadas em texto com botões para participação.
2. **Lembretes por DM** – avisos pessoais agendados.
3. **Hábitos pessoais** – metas diárias com reações fofas.
4. **Rotinas da comunidade** – hábitos globais com anúncios, confirmações e leaderboard.

Use `/cerebroso` para ver uma visão geral diretamente no Discord com exemplos rápidos.

---

## 👥 Guia para membros

### Pomodoro de canal (`/pomodoro`)
- `/pomodoro iniciar`: começa uma sessão no canal atual, abre botões **Participar**/**Sair** e mostra a fase atual.
- `/pomodoro pausar`, `/pomodoro retomar`, `/pomodoro parar` e `/pomodoro reiniciar`: controlam o fluxo do ciclo.
- `/pomodoro status`: mostra o tempo restante, ciclo e participantes.

> Dica: o canal lembra a configuração de tempos. Se quiser ajustar, peça para a staff usar `/pomodoro set`.

### Lembretes pessoais (`/lembrete` – sempre por DM)
- `/lembrete criar texto:"Beber água" quando:"+45m"`: agende usando atalhos (+10m, +2h, +1d) ou horários completos (`HH:MM`, `YYYY-MM-DD HH:MM`).
- `/lembrete listar`: veja seus próximos lembretes (máx. 10).
- `/lembrete cancelar id:<n>`: cancele um lembrete específico.

### Hábitos pessoais (`/habito`)
- `/habito criar nome:"Água" meta:8 intervalo_minutos:60 emoji:"💧"`: define a meta diária, intervalo de lembrete e o emoji usado nas confirmações.
- `/habito listar`: mostra o progresso do dia e o próximo lembrete.
- `/habito marcar id:<n> quantidade:<opcional>`: incrementa a meta manualmente.
- `/habito pausar`, `/habito retomar`, `/habito meta`, `/habito deletar`: ajuste seu hábito quando quiser.
- Reaja com o emoji sugerido nas mensagens do bot para marcar 1x concluído e receber um elogio fofo.

### Rotinas da comunidade (`/rotina`)
- `/rotina entrar nome_ou_id:"Escovar os dentes" intervalo_minutos:90 dm:true`: entra em uma rotina com lembretes por DM.
- `/rotina preferencias nome_ou_id:<rotina> intervalo_minutos:<n> dm:<true/false> janela_inicio:<HH:MM> janela_fim:<HH:MM>`: personalize frequência e janelas quietas.
- `/rotina sair`: abandone a rotina quando quiser.
- `/rotina meus`: lista todas as rotinas nas quais você está inscrito.
- `/rotina leaderboard`: ranking geral; `/rotina leaderboard nome:"Escovar os dentes"`: ranking da rotina específica.
- **Conquistas**: rotinas podem dar cargos especiais por streaks e por terminar o mês no topo. Basta continuar confirmando diariamente!

### Como confirmar rotinas
- Quando o bot anunciar a rotina no canal configurado, clique no botão **Fiz!** ou reaja com o emoji da rotina.
- Confirmou? Seus lembretes por DM pausam até o próximo dia.

---

## 🛠️ Guia para a staff

### Configuração inicial
1. Adicione o bot ao servidor com permissões de `Manage Roles`, `Manage Channels`, `Read Message History` e `Send Messages`.
2. Garanta que ele consiga adicionar/remover os cargos de conquistas (posicione o cargo do bot acima dos cargos de prêmio).
3. Execute `/syncfix` em cada servidor caso os comandos não apareçam imediatamente.

### Comandos administrativos gerais
- `/purgeglobal`: limpa quaisquer comandos globais duplicados e re-sincroniza todos os comandos do servidor atual.
- `/syncfix`: força a ressincronização de comandos nesta guild.
- `/debugslash`: lista no privado (ephemeral) todos os comandos carregados, útil para debug.

### Pomodoro
- `/pomodoro set foco:<min> pausa_curta:<min> pausa_longa:<min> ciclos:<int>`: ajusta a configuração padrão de um canal.

### Rotinas da comunidade
- `/rotina criar nome:<str> canal:<#canal> emoji:<emoji?> cargo:<cargo?> horarios:<HH:MM,...>`: cria uma rotina.
- `/rotina listar`: mostra todas as rotinas com status e cargos de conquistas.
- `/rotina pausar`, `/rotina retomar`, `/rotina deletar`: controle completo sobre as rotinas.
- `/rotina editar`: atualize nome, emoji, cargo, canal ou horários.
- `/rotina conquista_streak nome_ou_id:<rotina> dias:<n> cargo:<cargo>`: define o cargo entregue a quem atingir `n` dias consecutivos.
- `/rotina conquista_streak_remover nome_ou_id:<rotina>`: remove o cargo de streak configurado.
- `/rotina conquista_topmensal nome_ou_id:<rotina> cargo:<cargo>`: define o cargo de campeão do mês.
- `/rotina conquista_topmensal_remover nome_ou_id:<rotina>`: remove o cargo do top mensal.

### Manutenção e boas práticas
- Revise o arquivo `data/pomodoro_state.json` periodicamente para backups.
- Se algo parecer travado, reinicie o bot e use `/syncfix` para garantir que todos os comandos voltem a aparecer.
- Lembrete: as mensagens de staff são sempre *ephemeral*, evitando flood no chat.

---


## 🧰 Instalação e solução de problemas

### Como baixar o código com segurança
1. Faça o download do repositório completo com `git clone https://github.com/<seu-usuario>/cerebroso.git` **ou** baixe o ZIP diretamente da página do GitHub.
2. Evite copiar apenas o arquivo `cerebroso.py` usando links "raw" – provedores como GitHub podem responder `429: Too Many Requests` e salvar uma página de erro no lugar do código.
3. Execute `python doctor.py` para checar automaticamente se o download está íntegro. O script acusa qualquer vestígio de `429` ou HTML no início do arquivo.
4. Depois do download (ou se o `doctor.py` emitir alerta), confirme se a primeira linha de `cerebroso.py` é `import asyncio`. Se aparecer mensagem HTML ou `429`, refaça o download antes de rodar.

### Erro `429: Too Many Requests`
Esse erro significa que o servidor onde você baixou o arquivo bloqueou o acesso temporariamente, e o Python acabou lendo a página de aviso como se fosse código. Os sintomas mais comuns são mensagens como:

```
File "cerebroso.py", line 1
    429: Too Many Requests
    ^^^
SyntaxError: illegal target for annotation
```

ou até trechos de HTML no stacktrace, por exemplo `SyntaxError: invalid character '·' (U+00B7)` apontando para `<title>...`.

Quando isso acontecer, siga estes passos:
- aguarde alguns minutos e baixe novamente o arquivo **seguindo o passo a passo acima**;
- use `git clone`/ZIP, que baixam todos os arquivos de uma vez e evitam esse problema;
- rode `python doctor.py` depois do download: se ele acusar erro, apague o arquivo corrompido e repita o processo.

Após baixar corretamente, rode `pip install -r requirements.txt` e inicie o bot com `python cerebroso.py`.

---

## 📚 Exemplos rápidos
```
/pomodoro iniciar
/lembrete criar texto:"Alongar" quando:"18:00"
/habito criar nome:"Leitura" meta:1 intervalo_minutos:120 emoji:"📚"
/rotina entrar nome_ou_id:"Escovar os dentes" intervalo_minutos:60 dm:true
/rotina leaderboardgeral
```

Compartilhe este post com quem estiver começando agora. Quanto mais gente usando o Cerebroso, mais animadas ficam as rotinas e os rankings da comunidade!
