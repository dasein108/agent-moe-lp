from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Settings
from .logging_config import get_logger
from .snapshot import SnapshotService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch read-only Merchant Moe (Mantle) MNT/USDT wallet, pool, and LP metrics."
    )
    parser.add_argument(
        "--wallet",
        dest="wallet_address",
        help="Wallet address to inspect. Falls back to WALLET_ADDRESS from .env.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full snapshot JSON to stdout.",
    )
    parser.add_argument(
        "--save",
        metavar="REL_PATH",
        help="Save the snapshot under data/REL_PATH instead of data/latest_snapshot.json.",
    )
    return parser


def render_summary(snapshot: dict) -> str:
    pool = snapshot["pool"]
    lines = [
        f"RPC: {snapshot['chain']['rpc_url']}",
        f"Chain ID: {snapshot['chain']['chain_id']} "
        f"(expected {snapshot['chain']['expected_chain_id']})",
        f"Pool: {pool['pair_address']}",
        f"Tokens: {pool['token_x']['symbol']}/{pool['token_y']['symbol']}",
        f"Active bin: {pool['active_bin_id']}",
        f"Bin step: {pool['bin_step']}",
        f"Price ({pool['token_y']['symbol']} per {pool['token_x']['symbol']}): {pool['price_y_per_x']}",
    ]

    if pool.get("mnt_price_usdt") is not None:
        lines.append(f"Estimated MNT price in USDT: {pool['mnt_price_usdt']}")

    if snapshot["wallet"]:
        wallet = snapshot["wallet"]
        position = snapshot["position"]
        lines.extend(
            [
                f"Wallet: {wallet['address']}",
                f"MNT balance: {wallet['native_mnt']['normalized']}",
                f"WMNT balance: {wallet['wmnt']['normalized']}",
                f"USDT balance: {wallet['usdt']['normalized']}",
                f"Estimated wallet value in USDT: {wallet['estimated_total_value_usdt']}",
                f"LP exists: {position['position_exists']}",
            ]
        )
        if position["position_exists"]:
            lines.extend(
                [
                    f"LP bin range: {position['min_bin_id']} -> {position['max_bin_id']}",
                    f"In range: {position['in_range']}",
                    f"Active bins held: {len(position['active_bins'])}",
                ]
            )
            if position.get("inventory_included", True):
                lines.extend(
                    [
                        f"Estimated LP token_x: {position['estimated_token_x']}",
                        f"Estimated LP token_y: {position['estimated_token_y']}",
                    ]
                )
    return "\n".join(lines)


def main() -> None:
    args = build_parser().parse_args()
    settings = Settings.from_env().with_wallet(args.wallet_address)
    service = SnapshotService(settings)
    snapshot = service.build()

    if args.save:
        output_path = service.save(snapshot, args.save)
    else:
        output_path = service.save(snapshot)

    if args.json:
        print(json.dumps(snapshot, indent=2, sort_keys=True))
    else:
        logger = get_logger("cli")
        print(render_summary(snapshot))
        logger.info("Snapshot completed", extra={"output_path": str(output_path)})


if __name__ == "__main__":
    main()
