from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def test_warn_legacy_data_artifacts_reports_stale_jsonl_files(tmp_path):
    from moe_mantle_bot.farm_bot import FarmBot

    (tmp_path / "farm_history.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "farm_operations.jsonl").write_text("{}\n", encoding="utf-8")

    bot = MagicMock(spec=FarmBot)
    bot.settings = SimpleNamespace(data_dir=tmp_path)
    bot._warn_legacy_data_artifacts = FarmBot._warn_legacy_data_artifacts.__get__(bot)

    with patch("moe_mantle_bot.farm_bot.logger.warning") as warning_log:
        bot._warn_legacy_data_artifacts()

    messages = [call.args[0] for call in warning_log.call_args_list]
    assert len(messages) == 2
    assert any("farm_history.jsonl" in message for message in messages)
    assert any("farm_operations.jsonl" in message for message in messages)
    assert any("analytics.db" in message for message in messages)
