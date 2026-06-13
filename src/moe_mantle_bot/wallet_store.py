from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from web3 import Web3

from .utils import utc_now_iso


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
    def from_file(cls, path: Path) -> "WalletRecord":
        payload = json.loads(path.read_text(encoding="utf-8"))
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
