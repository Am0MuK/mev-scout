from decimal import Decimal
import json
import os
from unittest.mock import MagicMock, patch
import pytest

from mev_scout.cli import main
from mev_scout.store import Store


def test_cli_missing_etherscan_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.setenv("MEVSCOUT_RPC_146", "https://rpc.sonic.example.com")
    db_file = str(tmp_path / "scout.db")

    with pytest.raises(SystemExit) as exc:
        main(["fetch", "--chain", "146", "--db", db_file])
    assert exc.value.code == 2


def test_cli_fetch_blockscout_no_etherscan_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.setenv("MEVSCOUT_RPC_8453", "https://rpc.base.example.com")
    db_file = str(tmp_path / "scout.db")

    with patch("mev_scout.cli.fetch_cmd") as mock_fetch:
        with pytest.raises(SystemExit) as exc:
            main(["fetch", "--chain", "8453", "--db", db_file])
        assert exc.value.code == 0
        assert mock_fetch.call_count == 1


def test_cli_missing_rpc_url(monkeypatch, tmp_path):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "testkey")
    monkeypatch.delenv("MEVSCOUT_RPC_146", raising=False)
    db_file = str(tmp_path / "scout.db")

    with pytest.raises(SystemExit) as exc:
        main(["fetch", "--chain", "146", "--db", db_file])
    assert exc.value.code == 2


def test_cli_unsupported_chain(monkeypatch, tmp_path):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "testkey")
    monkeypatch.setenv("MEVSCOUT_RPC_9999", "https://rpc.example.com")
    db_file = str(tmp_path / "scout.db")

    with pytest.raises(SystemExit) as exc:
        main(["fetch", "--chain", "9999", "--db", db_file])
    assert exc.value.code == 2


def test_cli_fetch_success(monkeypatch, tmp_path):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "testkey")
    monkeypatch.setenv("MEVSCOUT_RPC_146", "https://rpc.sonic.example.com")
    db_file = str(tmp_path / "scout.db")

    with patch("mev_scout.cli.fetch_cmd") as mock_fetch:
        with pytest.raises(SystemExit) as exc:
            main(["fetch", "--chain", "146", "--days", "30", "--db", db_file])
        assert exc.value.code == 0
        assert mock_fetch.call_count == 1


def test_cli_value_success(monkeypatch, tmp_path):
    monkeypatch.setenv("MEVSCOUT_RPC_146", "https://rpc.sonic.example.com")
    db_file = str(tmp_path / "scout.db")

    with patch("mev_scout.cli.value_cmd") as mock_value:
        with pytest.raises(SystemExit) as exc:
            main(["value", "--chain", "146", "--db", db_file])
        assert exc.value.code == 0
        assert mock_value.call_count == 1


def test_cli_report_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "testkey")
    monkeypatch.setenv("MEVSCOUT_RPC_146", "https://rpc.sonic.example.com")
    db_file = str(tmp_path / "scout.db")

    with patch("mev_scout.cli.report_cmd") as mock_report:
        mock_report.return_value = "REPORT TEXT OUTPUT"
        with pytest.raises(SystemExit) as exc:
            main(["report", "--chain", "146", "--eurusd", "1.17", "--db", db_file])
        assert exc.value.code == 0
        out, _ = capsys.readouterr()
        assert "REPORT TEXT OUTPUT" in out


def test_cli_report_json_success(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "testkey")
    monkeypatch.setenv("MEVSCOUT_RPC_146", "https://rpc.sonic.example.com")
    db_file = str(tmp_path / "scout.db")

    with patch("mev_scout.cli.report_cmd") as mock_report:
        mock_report.return_value = json.dumps({"status": "ok"})
        with pytest.raises(SystemExit) as exc:
            main(["report", "--chain", "146", "--eurusd", "1.17", "--json", "--db", db_file])
        assert exc.value.code == 0
        out, _ = capsys.readouterr()
        assert '{"status": "ok"}' in out


def test_cli_coverage_error_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ETHERSCAN_API_KEY", "testkey")
    monkeypatch.setenv("MEVSCOUT_RPC_146", "https://rpc.sonic.example.com")
    db_file = str(tmp_path / "scout.db")

    with patch("mev_scout.cli.report_cmd", side_effect=Exception("Coverage gap(s) detected")):
        with pytest.raises(SystemExit) as exc:
            main(["report", "--chain", "146", "--eurusd", "1.17", "--db", db_file])
        assert exc.value.code == 2
        _, err = capsys.readouterr()
        assert "Coverage gap" in err
