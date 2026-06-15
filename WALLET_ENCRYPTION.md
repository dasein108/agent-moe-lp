---
title: Wallet Encryption
summary: How the Mantle signing wallet's private key is encrypted at rest (Ethereum V3 keystore — scrypt + AES-128-CTR) and decrypted at runtime via KEYSTORE_PASSWORD, plus how to manage it.
---

# Wallet Encryption

How the Merchant Moe (Mantle) farming bot's signing wallet is protected. The
private key is **encrypted at rest** in an Ethereum-standard V3 keystore file;
only a password (env var) is needed at runtime. There is no plaintext key on
disk in the normal path.

## TL;DR

- **At rest:** `wallet.json` — EIP V3 keystore, `scrypt` KDF + `AES-128-CTR`
  cipher, file mode `0600`, git-ignored.
- **At runtime:** `KEYSTORE_PASSWORD` (from `.env`) decrypts the keystore in
  memory. Key is never written back to disk.
- **Back-compat:** legacy plaintext `wallet.json` (`{address, private_key, …}`)
  still loads. The `PRIVATE_KEY` env var fallback is unchanged.
- **Code:** `src/moe_mantle_bot/wallet_store.py` (`WalletRecord`).

## 1. Storage format (at rest)

Standard Ethereum keystore (`eth_account.Account.encrypt`), version 3:

```json
{
  "address": "27e50dc5...",
  "crypto": {
    "cipher": "aes-128-ctr",
    "cipherparams": { "iv": "..." },
    "ciphertext": "...",
    "kdf": "scrypt",
    "kdfparams": { "dklen": 32, "n": 262144, "r": 8, "p": 1, "salt": "..." },
    "mac": "..."
  },
  "id": "...",
  "version": 3,
  "created_at": "2026-03-18T09:39:58+00:00",
  "source": "generated"
}
```

- **Cipher:** AES-128-CTR. **KDF:** scrypt (`n=262144`, `r=8`, `p=1`,
  `dklen=32`, random 16-byte salt) — `eth_account` defaults.
- **MAC:** integrity check; wrong password → MAC mismatch → decrypt fails
  closed (no silent partial decrypt).
- `created_at` / `source` are extra top-level keys we add to preserve our
  metadata; `Account.decrypt` ignores them.
- Permissions forced to `0o600` on write (`WalletRecord.save_encrypted`).

## 2. Encryption (one-time / migration)

`WalletRecord.save_encrypted(path, password, *, force=False)` —
`wallet_store.py`:

```python
keystore = Account.encrypt(self.private_key, password)  # scrypt + AES-128-CTR
keystore["created_at"] = self.created_at
keystore["source"] = self.source
path.write_text(json.dumps(keystore, indent=2) + "\n")
path.chmod(0o600)
```

## 3. Decryption (runtime)

`WalletRecord.from_file(path, password=None)` — `wallet_store.py`:

- Detects a V3 keystore (`crypto` block + `version == 3`).
- Password: `password` arg → `KEYSTORE_PASSWORD` env. Missing → `ValueError`.
- `Account.decrypt(payload, pwd)` → `0x`-prefixed hex key. Wrong password →
  `ValueError("Failed to decrypt keystore …")`.
- A legacy plaintext file (no `crypto` block) is loaded as before — no password
  needed.

All existing callers (`core/wallet.load_wallet`, `command_cli`,
`quant/wide_range_lp_manager`) pass no password and therefore decrypt
transparently via the `KEYSTORE_PASSWORD` env var. `TxSender` consumes
`wallet.private_key` (the in-memory decrypted key) unchanged.

## 4. CLI — wallet management

```bash
# encrypt the current wallet.json in place (writes wallet.json.plain.bak backup,
# verifies the keystore round-trips before reporting success)
moe wallet encrypt
moe wallet encrypt --out wallet.enc.json        # write to a different path
moe wallet encrypt --password '...'             # override KEYSTORE_PASSWORD
moe wallet encrypt --no-backup                  # skip the plaintext backup

# create a new wallet directly as an encrypted keystore
moe wallet create --encrypt

# show address (decrypts via KEYSTORE_PASSWORD; never prints the key)
moe wallet show --json
```

## 5. Runtime env

```bash
KEYSTORE_PASSWORD=...     # required to unlock an encrypted wallet.json
# fallback only (no wallet file): plaintext key
PRIVATE_KEY=0x...
WALLET_FILE=wallet.json   # optional; default ./wallet.json
```

`KEYSTORE_PASSWORD` lives in `.env`. The container reads it both via
`env_file: .env` and `load_dotenv()` on `/app/.env` (bind-mounted), so the
password reaches the bot on startup.

## 6. Recovery

`moe wallet encrypt` leaves a `wallet.json.plain.bak` plaintext copy (mode
`0600`, git-ignored via `*.plain.bak`). To roll back, copy it over
`wallet.json`. **Delete it once you've confirmed the encrypted wallet works and
you have the password stored safely** — it is an unencrypted private key.

## Operational notes

- `make deploy-files` scp's the **encrypted** `wallet.json` and local `.env`
  (carrying `KEYSTORE_PASSWORD`) to prod. After deploy, recreate the container
  (`docker-compose up -d --force-recreate`) so it reloads the new `.env` and
  wallet.
- Encryption protects the wallet file at rest (accidental commit, leaked
  backup, image layers). It does **not** protect against full server
  compromise — the password sits in `.env` on the same host.

## Hardening candidates

- [ ] `make deploy-files` chmods `.env` / `wallet.json` to `644` (world-readable)
      on the server. They are **bind-mounted read-only into a container that runs
      as the non-root `moebot` user**, so plain `chmod 600` (root-owned) makes the
      files unreadable to the bot (`Permission denied: /app/.env`). To tighten,
      `chown` the host files to the uid `moebot` maps to (then `600`), or run the
      container as root — do not just lower the mode.
- [ ] Document a rotation policy for `KEYSTORE_PASSWORD`.
- [ ] Confirm prod runs via keystore, not the `PRIVATE_KEY` plaintext fallback.
