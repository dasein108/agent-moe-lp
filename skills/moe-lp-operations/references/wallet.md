# Wallet Management

How the bot loads, creates, and selects the signing wallet. Every read/write
operation resolves a wallet the same way, and **`wallet.json` in the repo root is the
default for both reads and writes**.

## ⚠️ Security first

- `wallet.json` holds the signing key with `0o600` (owner read/write only) and is
  git-ignored. It may be an **encrypted Ethereum V3 keystore** (decrypted at runtime
  via `KEYSTORE_PASSWORD`) or a legacy **plaintext** file — both load transparently.
  Encrypt an existing wallet with `moe wallet encrypt` (see `WALLET_ENCRYPTION.md`).
  Never commit it, print the private key, paste it into chat/logs, or send it anywhere.
- `moe wallet show` prints only **metadata (address + file path)**, never the key —
  prefer it when you just need the address.
- Read-only inspection (`moe snapshot`, `moe-readonly`, pool/market analysis) needs
  only an **address**, not the key. Use the key-bearing wallet only for mutations
  (`moe lp`, `moe swap/wrap/unwrap`, `moe-farm --live`).

## How a wallet is resolved (every command)

Order of precedence, highest first:
1. `--wallet-file <path>` global flag on `moe`
2. `WALLET_FILE` env var (in `.env`)
3. **`wallet.json`** in the working directory (the default)

If the resolved file does not exist, the bot falls back to a `PRIVATE_KEY` env var.
If neither a file nor `PRIVATE_KEY` is available, a mutating command fails with
`wallet.json not found and PRIVATE_KEY is not set`.

So by default — no flags, no env overrides — the bot reads from and writes (a newly
created wallet) to `wallet.json`.

## Create a new wallet

```bash
moe wallet create --json                 # writes to the default wallet.json (0o600)
moe wallet create --out hot-wallet.json  # write to a specific path instead
moe wallet create --force                # overwrite an existing wallet file
```
- Generates a fresh keypair and saves it (address + private key) to `--out`, else
  `--wallet-file`, else `settings.wallet_file` (= `wallet.json`).
- **Refuses to overwrite** an existing file unless `--force` — this prevents clobbering
  a funded wallet. Before using `--force`, confirm you are not destroying a wallet that
  holds funds (check `moe wallet show` / its on-chain balance first).
- After creating, fund the address with native MNT (for gas) and the tokens you intend
  to LP before running live operations.

## Use / inspect the current wallet

```bash
moe wallet show --json        # address + which file is in use (no private key)
moe snapshot --json           # uses the default-resolved wallet's address
moe balance --json            # aggregate balances for the resolved wallet
```

## Use a different wallet for a command

```bash
moe --wallet-file hot-wallet.json snapshot --json     # read with a chosen wallet
moe --wallet-file hot-wallet.json lp add ... --dry-run # operate with a chosen wallet
```
Or set `WALLET_FILE=...` in `.env` to change the default for every command. The farm
loop (`moe-farm`) uses the same resolution; point it at a wallet via `WALLET_FILE`.

## Read-only against an arbitrary address (no key)

```bash
moe-readonly --wallet 0x<address>          # snapshot any address, no wallet file needed
moe snapshot --wallet 0x<address> --json   # pool/position view for that address
```

## Programmatic

```python
from moe_mantle_bot.config import Settings
from moe_mantle_bot.core.wallet import load_wallet          # file → else PRIVATE_KEY
from moe_mantle_bot.wallet_store import WalletRecord

settings = Settings.from_env()
wallet = load_wallet(settings)              # resolves wallet.json / WALLET_FILE / key
addr = wallet.address

WalletRecord.create()                       # new keypair in memory
WalletRecord.from_file(path)                # load a specific file
WalletRecord.from_private_key(pk)           # from a raw key
record.save(path, force=False)              # writes 0o600; force to overwrite
```
`WalletRecord.create().save(path)` is exactly what `moe wallet create` does.
