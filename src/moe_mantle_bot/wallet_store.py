from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from eth_account import Account
from web3 import Web3

from .utils import utc_now_iso


def _is_keystore(payload: object) -> bool:
    """True if `payload` is an Ethereum V3 keystore (encrypted at rest)."""
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("crypto"), dict)
        and payload.get("version") == 3
    )


@dataclass(frozen=True)
class WalletRecord:
    address: str
    private_key: str
    created_at: str
    source: str = "generated"

    @classmethod
    def create(cls) -> "WalletRecord":
        w3 = Web3()
        account = w3.eth.account.create()
        return cls(
            address=account.address,
            private_key=account.key.hex(),
            created_at=utc_now_iso(),
        )

    @classmethod
    def from_file(cls, path: Path, password: str | None = None) -> "WalletRecord":
        """Load a wallet from disk.

        Supports two on-disk formats transparently:
        - Encrypted Ethereum V3 keystore (scrypt + AES-128-CTR). Decrypted with
          `password`, falling back to the ``KEYSTORE_PASSWORD`` env var.
        - Legacy plaintext ``{address, private_key, ...}`` JSON (back-compat).
        """
        payload = json.loads(path.read_text(encoding="utf-8"))

        if _is_keystore(payload):
            pwd = password if password is not None else os.getenv("KEYSTORE_PASSWORD")
            if not pwd:
                raise ValueError(
                    f"{path} is an encrypted keystore but no password was provided "
                    "(set KEYSTORE_PASSWORD or pass password=...)."
                )
            try:
                key = Account.decrypt(payload, pwd)
            except ValueError as exc:
                raise ValueError(f"Failed to decrypt keystore {path}: {exc}") from exc
            raw_address = str(payload["address"])
            address = raw_address if raw_address.startswith("0x") else f"0x{raw_address}"
            return cls(
                address=Web3.to_checksum_address(address),
                private_key=Web3.to_hex(key),
                created_at=payload.get("created_at", utc_now_iso()),
                source=payload.get("source", "keystore"),
            )

        # Legacy plaintext format.
        return cls(
            address=payload["address"],
            private_key=payload["private_key"],
            created_at=payload.get("created_at", utc_now_iso()),
            source=payload.get("source", "file"),
        )

    @classmethod
    def from_private_key(cls, private_key: str) -> "WalletRecord":
        """Create wallet record from private key."""
        w3 = Web3()
        account = w3.eth.account.from_key(private_key)
        return cls(
            address=account.address,
            private_key=private_key,
            created_at=utc_now_iso(),
            source="private_key"
        )

    def save(self, path: Path, *, force: bool = False) -> None:
        if path.exists() and not force:
            raise FileExistsError(f"{path} already exists")
        path.write_text(json.dumps(asdict(self), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        path.chmod(0o600)

    def save_encrypted(self, path: Path, password: str, *, force: bool = False) -> None:
        """Write the wallet as an encrypted Ethereum V3 keystore.

        Uses ``eth_account``'s default scrypt KDF (n=2**18, r=8, p=1) and
        AES-128-CTR cipher. Our ``created_at``/``source`` metadata is stored as
        extra top-level keys (ignored by ``Account.decrypt``). File mode 0o600.
        Wrong password fails closed on decrypt (MAC mismatch) — never silent.
        """
        if not password:
            raise ValueError("A non-empty password is required to encrypt the wallet.")
        if path.exists() and not force:
            raise FileExistsError(f"{path} already exists")
        keystore = Account.encrypt(self.private_key, password)
        keystore["created_at"] = self.created_at
        keystore["source"] = self.source
        path.write_text(json.dumps(keystore, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
