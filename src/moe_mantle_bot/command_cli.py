from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from web3 import Web3

from .balance_manager import BalanceManager
from .cli import render_summary
from .config import Settings
from .lp_service import LPService, resolve_pool_token_roles
from .models import ExecutionResult
from .rpc_client import RpcClient
from .snapshot import SnapshotService
from .tx_sender import (
    PreviewValidationError,
    TransactionExecutionError,
    TxSender,
    serialize_execution_error,
)
from .core.wallet import load_wallet
from .logging_config import get_logger
from .wallet_store import WalletRecord


def _decimal(value: str) -> Decimal:
    return Decimal(value)


def _wallet_record_from_args(settings: Settings, wallet_file: Path | None) -> WalletRecord | None:
    path = wallet_file or settings.wallet_file
    if path.exists():
        return WalletRecord.from_file(path)
    return None


def _require_wallet(settings: Settings, wallet_file: Path | None) -> WalletRecord:
    record = _wallet_record_from_args(settings, wallet_file)
    if record is None and not settings.private_key:
        raise RuntimeError("wallet.json not found and PRIVATE_KEY is not set")
    if record is not None:
        return record
    w3 = Web3()
    account = w3.eth.account.from_key(settings.private_key or "")
    return WalletRecord(
        address=account.address,
        private_key=settings.private_key or "",
        created_at="",
        source="env",
    )


def _print_results(results: list[ExecutionResult], *, as_json: bool) -> None:
    payload = [
        {
            "action": result.action,
            "tx_hash": result.tx_hash,
            "dry_run": result.dry_run,
            "details": result.details,
        }
        for result in results
    ]
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    
    logger = get_logger("command_cli")
    for result in payload:
        logger.info("Operation completed", extra={
            "action": result['action'],
            "dry_run": result['dry_run'],
            "tx_hash": result['tx_hash'],
            "details": result["details"]
        })


def _print_cli_exception(exc: Exception) -> None:
    logger = get_logger("command_cli.error")
    logger.error("Exception occurred", extra=serialize_execution_error(exc))
    logger.debug("Exception traceback", exc_info=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merchant Moe (Mantle) wallet and execution CLI.")
    parser.add_argument("--wallet-file", type=Path, help="Path to wallet.json. Defaults to WALLET_FILE or wallet.json.")
    parser.add_argument("--debug", action="store_true", help="Print internal timing and discovery logs to stderr.")
    parser.add_argument(
        "--pool", metavar="ADDRESS", default=None,
        help="LB pair pool address to operate on (overrides POOL_ADDRESS). Tokens are "
             "auto-discovered on-chain; must be a WMNT-paired pool.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    wallet_parser = subparsers.add_parser("wallet", help="Wallet file management.")
    wallet_sub = wallet_parser.add_subparsers(dest="wallet_command", required=True)

    wallet_create = wallet_sub.add_parser("create", help="Create a new wallet.json file.")
    wallet_create.add_argument("--out", type=Path)
    wallet_create.add_argument("--force", action="store_true")
    wallet_create.add_argument("--json", action="store_true")

    wallet_show = wallet_sub.add_parser("show", help="Show the current wallet file metadata.")
    wallet_show.add_argument("--json", action="store_true")

    snapshot_parser = subparsers.add_parser("snapshot", help="Fetch pool and wallet state.")
    snapshot_parser.add_argument("--wallet", dest="wallet_address")
    snapshot_parser.add_argument(
        "--deep-position",
        action="store_true",
        help="Fall back to slow historical log scanning if near-active LP discovery finds nothing.",
    )
    snapshot_parser.add_argument(
        "--with-lp-inventory",
        action="store_true",
        help="Estimate underlying LP WMNT/USDT amounts. Slower because it reads reserves for each active bin.",
    )
    snapshot_parser.add_argument("--json", action="store_true")
    snapshot_parser.add_argument("--save", metavar="REL_PATH")

    balance_parser = subparsers.add_parser("balance", help="Show aggregate balances: wallet + LP + fees.")
    balance_parser.add_argument("--json", action="store_true")

    wrap_parser = subparsers.add_parser("wrap", help="Wrap native MNT into WMNT.")
    wrap_parser.add_argument("--amount-mnt", type=_decimal, required=True)
    wrap_parser.add_argument("--dry-run", action="store_true")
    wrap_parser.add_argument("--json", action="store_true")

    unwrap_parser = subparsers.add_parser("unwrap", help="Unwrap WMNT into native MNT.")
    unwrap_parser.add_argument("--amount-wmnt", type=_decimal, required=True)
    unwrap_parser.add_argument("--dry-run", action="store_true")
    unwrap_parser.add_argument("--json", action="store_true")

    swap_parser = subparsers.add_parser("swap", help="Swap between WMNT and USDT.")
    swap_parser.add_argument("--from-token", choices=["wmnt", "usdt"], required=True)
    swap_parser.add_argument("--amount", type=_decimal, required=True)
    swap_parser.add_argument("--slippage-bps", type=int)
    swap_parser.add_argument("--dry-run", action="store_true")
    swap_parser.add_argument("--json", action="store_true")

    lp_parser = subparsers.add_parser("lp", help="LP operations.")
    lp_sub = lp_parser.add_subparsers(dest="lp_command", required=True)

    lp_add = lp_sub.add_parser("add", help="Create a WMNT/USDT LP position.")
    lp_add.add_argument("--amount-wmnt", type=_decimal, required=True)
    lp_add.add_argument("--amount-usdt", type=_decimal, required=True)
    lp_add.add_argument("--bin-count", type=int)
    lp_add.add_argument("--wrap-mnt", action="store_true")
    lp_add.add_argument("--auto-rebalance", action="store_true", help="Automatically rebalance portfolio if insufficient MNT for LP creation")
    lp_add.add_argument("--slippage-bps", type=int)
    lp_add.add_argument("--dry-run", action="store_true")
    lp_add.add_argument("--json", action="store_true")

    lp_remove = lp_sub.add_parser("remove", help="Withdraw the current LP position.")
    lp_remove.add_argument("--slippage-bps", type=int)
    lp_remove.add_argument("--max-bins-per-tx", type=int, default=50)
    lp_remove.add_argument("--dry-run", action="store_true")
    lp_remove.add_argument("--json", action="store_true")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = Settings.from_env().with_wallet_file(args.wallet_file).with_debug(args.debug)
    if args.pool:
        # Override pool and resolve its cash/quote token from on-chain tokenX/tokenY.
        settings = replace(settings, pool_address=args.pool)
        settings = resolve_pool_token_roles(RpcClient(settings), settings)

    if args.command == "wallet":
        if args.wallet_command == "create":
            wallet = WalletRecord.create()
            output_path = args.out or args.wallet_file or settings.wallet_file
            wallet.save(output_path, force=args.force)
            payload = {"path": str(output_path), "address": wallet.address, "created_at": wallet.created_at}
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                logger = get_logger("command_cli")
                logger.info("Wallet created", extra={
                    "output_path": str(output_path),
                    "address": wallet.address,
                    "private_key_stored": True
                })
            return

        wallet = _require_wallet(settings, args.wallet_file)
        payload = {
            "address": wallet.address,
            "created_at": wallet.created_at,
            "source": wallet.source,
            "wallet_file": str(args.wallet_file or settings.wallet_file),
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            logger = get_logger("command_cli")
            logger.info("Wallet info", extra={
                "wallet_file": payload['wallet_file'],
                "address": wallet.address,
                "source": wallet.source
            })
        return

    if args.command == "snapshot":
        wallet_record = _wallet_record_from_args(settings, args.wallet_file)
        wallet_address = args.wallet_address or (wallet_record.address if wallet_record else None)
        snapshot_settings = settings.with_wallet(wallet_address)
        service = SnapshotService(snapshot_settings)
        snapshot = service.build(
            wallet_address=wallet_address,
            deep_position_search=args.deep_position,
            include_position_inventory=args.with_lp_inventory,
        )
        output_path = service.save(snapshot, args.save) if args.save else service.save(snapshot)
        if args.json:
            print(json.dumps(snapshot, indent=2, sort_keys=True))
        else:
            logger = get_logger("command_cli")
            print(render_summary(snapshot))
            logger.info("Snapshot saved", extra={"output_path": str(output_path)})
        return

    if args.command == "balance":
        wallet_record = _require_wallet(settings, args.wallet_file)
        rpc = RpcClient(settings)
        tx = TxSender(rpc, wallet_record, settings)
        bm = BalanceManager(rpc, tx, settings)
        lp = LPService(rpc, tx, bm, settings)

        wallet_addr = wallet_record.address
        balances = bm.get_wallet_balances(wallet_addr)
        position = lp.get_position(wallet_addr, include_inventory=True)
        mnt_price = float(balances.mnt_price_usdt or 0)

        # Wallet
        native_mnt = float(balances.native_mnt.normalized)
        wmnt = float(balances.wmnt.normalized)
        usdt = float(balances.usdt.normalized)
        wallet_mnt_total = native_mnt + wmnt

        # LP position
        lp_mnt = float(position.estimated_token_x or 0) if position.position_exists else 0
        lp_usdt = float(position.estimated_token_y or 0) if position.position_exists else 0

        # Totals
        total_mnt = wallet_mnt_total + lp_mnt
        total_usdt = usdt + lp_usdt
        total_value = total_mnt * mnt_price + total_usdt

        result = {
            "mnt_price_usdt": round(mnt_price, 6),
            "wallet": {
                "native_mnt": round(native_mnt, 4),
                "wmnt": round(wmnt, 4),
                "total_mnt": round(wallet_mnt_total, 4),
                "usdt": round(usdt, 4),
                "value_usdt": round(wallet_mnt_total * mnt_price + usdt, 2),
            },
            "lp": {
                "mnt": round(lp_mnt, 4),
                "usdt": round(lp_usdt, 4),
                "value_usdt": round(lp_mnt * mnt_price + lp_usdt, 2),
                "in_range": position.in_range if position.position_exists else None,
                "bins": position.bin_count if position.position_exists else 0,
            },
            "total": {
                "mnt": round(total_mnt, 4),
                "usdt": round(total_usdt, 4),
                "value_usdt": round(total_value, 2),
            },
        }

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"MNT price: ${mnt_price:.4f}")
            print()
            print(f"=== Wallet ===")
            print(f"  Native MNT:  {native_mnt:>12.2f}")
            print(f"  WMNT:        {wmnt:>12.2f}")
            print(f"  Total MNT:   {wallet_mnt_total:>12.2f}  (${wallet_mnt_total * mnt_price:.2f})")
            print(f"  USDT:        {usdt:>12.2f}")
            print()
            print(f"=== LP Position ===")
            if position.position_exists:
                print(f"  MNT in LP:   {lp_mnt:>12.2f}  (${lp_mnt * mnt_price:.2f})")
                print(f"  USDT in LP:  {lp_usdt:>12.2f}")
                print(f"  In range:    {'YES' if position.in_range else 'NO'}")
                print(f"  Bins:        {position.bin_count}")
            else:
                print(f"  No active position")
            print()
            print(f"=== Total ===")
            print(f"  MNT:         {total_mnt:>12.2f}  (${total_mnt * mnt_price:.2f})")
            print(f"  USDT:        {total_usdt:>12.2f}")
            print(f"  Total value: ${total_value:>11.2f}")
        return

    if args.command in ("wrap", "unwrap", "swap"):
        wallet = _require_wallet(settings, args.wallet_file)
        rpc = RpcClient(settings)
        tx = TxSender(rpc, wallet, settings)
        bm = BalanceManager(rpc, tx, settings)

        if args.command == "wrap":
            _print_results([bm.wrap_mnt(args.amount_mnt, dry_run=args.dry_run)], as_json=args.json)
            return

        if args.command == "unwrap":
            _print_results([bm.unwrap_wmnt(args.amount_wmnt, dry_run=args.dry_run)], as_json=args.json)
            return

        # swap
        token_in = settings.wmnt_address if args.from_token == "wmnt" else settings.usdt_address
        token_out = settings.usdt_address if args.from_token == "wmnt" else settings.wmnt_address
        results = bm.swap(
            token_in=token_in,
            token_out=token_out,
            amount_in=args.amount,
            slippage_bps=args.slippage_bps,
            dry_run=args.dry_run,
        )
        _print_results(results, as_json=args.json)
        return

    if args.command == "lp":
        try:
            wallet = load_wallet(settings)
            rpc = RpcClient(settings)
            tx = TxSender(rpc, wallet, settings)
            bm = BalanceManager(rpc, tx, settings)
            lp = LPService(rpc, tx, bm, settings)

            if args.lp_command == "add":
                # Auto-rebalance before LP creation if requested
                if getattr(args, 'auto_rebalance', False) and not args.dry_run:
                    bm.rebalance_if_needed(wallet.address, tolerance_bps=200, dry_run=False)

                results = lp.create_position(
                    amount_wmnt=args.amount_wmnt,
                    amount_usdt=args.amount_usdt,
                    bin_count=args.bin_count,
                    slippage_bps=args.slippage_bps,
                    dry_run=args.dry_run,
                )
                _print_results(results, as_json=args.json)
                return

            # lp remove
            results = lp.remove_position(
                slippage_bps=args.slippage_bps,
                dry_run=args.dry_run,
                max_bins_per_tx=args.max_bins_per_tx,
            )
            _print_results(results, as_json=args.json)
            return
        except (PreviewValidationError, TransactionExecutionError) as exc:
            _print_cli_exception(exc)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
