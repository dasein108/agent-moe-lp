from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from moe_mantle_bot.tx_sender import TransactionExecutionError, TxSender


def test_send_blocks_when_wallet_cannot_cover_fixed_gas_limit():
    rpc = MagicMock()
    rpc.w3 = MagicMock()
    rpc.w3.eth = MagicMock()
    rpc.w3.eth.account = MagicMock()
    account = MagicMock()
    account.address = "0xWALLET"
    rpc.w3.eth.account.from_key.return_value = account
    rpc.w3.eth.get_transaction_count.return_value = 7
    rpc.w3.eth.get_balance.return_value = 100

    settings = MagicMock()
    settings.chain_id = 143

    wallet = SimpleNamespace(private_key="0x" + "11" * 32)
    sender = TxSender(rpc, wallet, settings)
    sender.gas_price_params = MagicMock(return_value={"gasPrice": 10})

    function = MagicMock()
    function.build_transaction.side_effect = lambda tx: dict(tx)

    with pytest.raises(TransactionExecutionError) as excinfo:
        sender.send(
            "remove_liquidity",
            function,
            dry_run=False,
            gas_limit=20,
            details={"preflight": {"bin_count": 22}},
        )

    err = excinfo.value
    assert err.stage == "native_balance_precheck"
    assert err.retryable is False
    assert "insufficient native MNT" in str(err)
    account.sign_transaction.assert_not_called()
    rpc.w3.eth.send_raw_transaction.assert_not_called()
