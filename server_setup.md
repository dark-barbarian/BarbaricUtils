## Start bot script
```bash
nano /home/botuser/DiscordBots/start_bot.sh
```
```bash
#!/bin/bash

BOT_NAME="$1"
BASE_DIR="/home/botuser/DiscordBots/$BOT_NAME"

# Activate venv
source "$BASE_DIR/venv/bin/activate"

# Run the bot
exec python "$BASE_DIR/main.py"
```
```bash
chmod +x /home/botuser/DiscordBots/start_bot.sh
```

---

## Environment File  
```bash
nano /etc/BarbaricUtils.env
```
```ini
DISCORD_TOKEN=xxx
```
```bash
chmod 600 /etc/BarbaricUtils.env
chown root:root /etc/BarbaricUtils.env
```

---

## Service File  
```bash
nano /etc/systemd/system/discordbot@.service
```
```ini
[Unit]
Description=Discord Bot - %i
After=network.target

[Service]
Type=simple
User=botuser
WorkingDirectory=/home/botuser/DiscordBots/%i
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/etc/%i.env
ExecStart=/home/botuser/DiscordBots/start_bot.sh %i
Restart=always
RestartSec=5
MemoryAccounting=true
CPUAccounting=true

[Install]
WantedBy=multi-user.target
```

---

## Enable & Start Service
```bash
systemctl daemon-reload
systemctl enable --now discordbot@BarbaricUtils
```

---

## Store persistent data to GitHub automatically
[git-auto-push.sh](git-auto-push.sh)

```bash
nano /home/botuser/DiscordBots/BarbaricUtils/git-auto-push.sh
chmod +x /home/botuser/DiscordBots/BarbaricUtils/git-auto-push.sh
nano /etc/systemd/system/git-auto-commit-push.service
```

```ini
[Unit]
Description=Monthly git auto-commit of persistent folder
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/home/botuser/DiscordBots/BarbaricUtils/git-auto-push.sh
User=botuser
WorkingDirectory=/home/botuser/DiscordBots/BarbaricUtils
```

```bash
nano /etc/systemd/system/git-auto-commit-push.timer
```
```ini
[Unit]
Description=Run git auto-commit once per month

[Timer]
OnCalendar=monthly
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload
systemctl enable --now git-auto-commit-push.timer
```

---

## Check Status & Logs
```bash
systemctl status discordbot@BarbaricUtils
journalctl -u discordbot@BarbaricUtils -f
journalctl -u discordbot@BarbaricUtils -n 50 --no-pager
```

---

## Restart & Stop
```bash
systemctl restart discordbot@BarbaricUtils
systemctl stop discordbot@BarbaricUtils
systemctl disable discordbot@BarbaricUtils
```

---

## List All Services
```bash
systemctl list-units --type=service
systemctl list-timers
```
