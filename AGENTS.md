# Repository Guidelines

## Purpose
This repo supports development of quantitative liquidity strategies for the Merchant Moe `WMNT/USDT` Liquidity Book pool on Mantle. Main goals: improve narrow-range and wide-range fee farming, and operational safety of LP execution, rebalancing, registry tracking, and analytics. Read [README.md](README.md), [docs/strategy-guide.md](docs/strategy-guide.md), and [docs/moe-lp-mechanics.md](docs/moe-lp-mechanics.md) before changing strategy logic.

## Project Structure & Key References
Core application code: `src/moe_mantle_bot/`. Start with `farm_bot.py` for high-level orchestration, `orchestration/` for cycle preparation/planning/finalization seams, `execution/` for single-position execution, `strategies/` for active strategy services, `lp_service.py` for LP math/execution, `balance_manager.py` for budgets and rebalancing, `quant/` for Keltner, candle fetching, and decision logic. Tests: `tests/`. Operational scripts: `scripts/`. Persistent state: `data/`. Docs: `docs/`.

Active work targets the current single-position path.

Important references:
- [docs/technical-reference.md](docs/technical-reference.md): Module map and data models
- [docs/configuration.md](docs/configuration.md): Env vars and strategy parameters
- [docs/cli-reference.md](docs/cli-reference.md): Executable workflows
- [docs/deployment.md](docs/deployment.md): Docker and server operations

## Development Workflow
Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest
```

Useful checks:
- `moe snapshot --with-lp-inventory`
- `moe-farm --once --json`
- `pytest -v`
- `make help`

Prefer dry-run flows before any live command. For container work, use `docker compose build` and `docker compose run --rm moe-farm-bot`. `make help` lists deployment and registry sync commands.

## Strategy & LP Rules
Merchant Moe LB positions are per-bin fungible balances, not NFTs. Positions from the same wallet in the same pool can merge on-chain, so `data/lp_registry.json` is the source of truth for position tracking and partial removal. Preserve registry hooks when editing LP creation or removal.

Respect LB bin composition rules from [docs/moe-lp-mechanics.md](docs/moe-lp-mechanics.md): bins above active are `WMNT`-only, bins below active are `USDT`-only, only the active bin can mix both. Most `LBRouter__WrongAmounts` failures come from violating that rule, using a stale active bin, or sending token amounts that don't match computed distributions.

Narrow strategy: concentrated center-heavy shapes. Wide strategy: fee farming driven by Keltner width and ranging-market confidence. Strategy can be auto-selected (default) or forced via `--strategy narrow|wide|auto`. When changing quant logic, verify how it affects `BIN_COUNT`, distribution shape, gas reserve (`GAS_RESERVE_MNT`), budget cap (`MAX_BUDGET_PCT`), native MNT min balance guard (`MNT_MIN_BALANCE`), dust bin filtering, and exit-and-reenter behavior.

Supported path: single-position orchestration.

## Coding, Testing, and Safety
Follow existing Python style: 4-space indentation, `snake_case` for functions/modules, `PascalCase` for classes, type hints on non-trivial code paths. Format with `black`, sort imports with `isort`, run `mypy` when touching typed modules or dataclasses.

Tests use `pytest`, descriptive `test_*` names, mocks/fixtures for RPC or contract calls. Add or update tests whenever changing LP math, registry persistence, strategy selection, or capital allocation.

Do not commit `.env`, private keys, or real wallet files. Treat `wallet.json`, `data/`, and server settings in `Makefile` as sensitive. Default to dry-run execution unless live trading is explicitly intended.
