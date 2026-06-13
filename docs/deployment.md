# Deployment

## Local Installation

### Requirements
- Python 3.11+
- Mantle RPC endpoint access
- Wallet with MNT + USDT

### Setup

```bash
# Clone and install
git clone <repo-url>
cd moe-mantle-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Configure
cp .env.example .env
# Edit .env with your settings (see configuration.md)

# Create wallet (or use existing)
moe wallet create

# Fund the wallet with MNT (gas + LP) and USDT (LP)
# Wallet address is shown after creation

# Verify
moe snapshot --with-lp-inventory
```

### Running

```bash
# Dry-run first
moe-farm --once --json

# Single live cycle
moe-farm --once --live

# Continuous farming
moe-farm --live --poll-interval-seconds 60
```

## Docker Deployment

### Makefile Workflow

For the production server, prefer `Makefile` targets over ad hoc `scp` or manual state edits:

```bash
# Show all available operations
make help

# Deploy source only
make update-sources

# Deploy .env only
make update-env

# Deploy source + .env
make update-code-and-env

# Backup then remove server registry + analytics DB
make reset-server-state

# Full reset + deploy + restart
make full-update
```

`make full-update` is the safest clean rollout when local and server state diverge. Backs up and removes `data/lp_registry.json` and `data/analytics.db` on the server, then pushes code and `.env`, then restarts containers.

### Build and Run

```bash
# Build
docker compose build

# Run (edit docker-compose.yml first)
docker compose up -d

# View logs
docker compose logs -f moe-farm-bot

# Stop
docker compose down
```

### docker-compose.yml

Compose file mounts:
- `./src` -- Source code (read-only)
- `./data` -- Persistent data (registry, snapshots, history)
- `./wallet.json` -- Wallet file (read-only)
- `./.env` -- Environment config (read-only)

### Custom Command

Override farming command in `docker-compose.yml`:

```yaml
command: ["python3", "-m", "moe_mantle_bot.farm_bot",
          "--live",
          "--poll-interval-seconds", "30"]
```

## systemd Service

For a Linux server without Docker:

```ini
# /etc/systemd/system/moe-farm.service
[Unit]
Description=Merchant Moe Mantle Farm Bot
After=network.target

[Service]
Type=simple
User=moebot
WorkingDirectory=/opt/moe-mantle-bot
Environment=PATH=/opt/moe-mantle-bot/.venv/bin
EnvironmentFile=/opt/moe-mantle-bot/.env
ExecStart=/opt/moe-mantle-bot/.venv/bin/moe-farm --live --poll-interval-seconds 60
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable moe-farm
sudo systemctl start moe-farm
sudo journalctl -u moe-farm -f
```

## Telegram Notifications

Bot sends alerts for LP operations, errors, cycle results.

### Setup

1. Create a Telegram bot via [@BotFather](https://t.me/BotFather)
2. Get the bot token
3. Create a channel/group and add the bot
4. Get the channel ID (use [@userinfobot](https://t.me/userinfobot) or API)

```bash
# .env
NOTIFICATIONS_ENABLED=true
TELEGRAM_NOTIFICATIONS_ENABLED=true
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHANNEL_ID=your_channel_id
```

### Alert Types

- **LP_ADD** -- Position created (amounts, bin range, gas cost)
- **LP_REMOVE** -- Position removed (recovered amounts, hold time, fees)
- **REBALANCE** -- Portfolio rebalanced to target ratio
- **ERROR** -- Operation failures with suggested actions

## Monitoring

### Key Files to Watch

```bash
# Latest state
cat data/latest_snapshot.json | jq .

# Position registry
cat data/lp_registry.json | jq .

# Runtime cycle log
tail -f data/farm_bot.log
```

### Quick Health Check

```bash
# Active position?
moe snapshot --json | jq '.position.position_exists'

# In range?
moe snapshot --json | jq '.position.in_range'

# Wallet value
moe snapshot --json | jq '.wallet'
```

The bot also runs registry reconciliation automatically on startup.

### State Sync

When moving state between local and server, sync registry and analytics DB together:

```bash
# Push local registry + analytics DB to server
make sync-state-to-server

# Pull server registry + analytics DB to local
make sync-state-from-server
```

Both create backups first. Analytics DB is copied via SQLite backup snapshot, not live file copy.

## Security

### Wallet Safety

- `wallet.json` must have `0o600` permissions (owner read/write only)
- Never commit `wallet.json` or `.env` to version control
- Both are in `.gitignore`

### Transaction Safety

- All operations default to `--dry-run`
- Must explicitly pass `--live` for real execution
- Gas reserve (`GAS_RESERVE_MNT`, default 2) prevents wallet drain
- Budget capped at 80% of wallet (`MAX_BUDGET_PCT`) as additional reserve
- Native MNT min balance guard auto-replenishes gas before each cycle
- Dust bin filter prevents contract reverts during LP removal
- Position size validated against `MIN_POSITION_SIZE_USDT` before creation
- Slippage tolerance limits adverse execution

### RPC Resilience

- 5 fallback RPC endpoints with automatic rotation
- Transient errors (timeout, rate limit, 502/503) trigger retry with next endpoint
- Non-transient errors (contract revert, insufficient funds) fail immediately
