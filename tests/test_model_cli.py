from __future__ import annotations

import json
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.model_adapter import load_model_config

runner = CliRunner()


class MockPongHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        payload = json.dumps({"choices": [{"message": {"content": "PONG"}}]}).encode("utf-8")
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def mock_pong_server() -> Generator[str, None, None]:
    server = HTTPServer(("127.0.0.1", 0), MockPongHandler)
    thread = Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    port = server.server_port
    endpoint = f"http://127.0.0.1:{port}"
    yield endpoint
    server.shutdown()
    server.server_close()


def test_model_cli_status_and_disable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    status_res = runner.invoke(app, ["model", "status", "--home", str(home)])
    assert status_res.exit_code == 0
    assert "disabled" in status_res.output

    config_res = runner.invoke(
        app,
        [
            "model",
            "configure",
            "--endpoint",
            "http://127.0.0.1:11434",
            "--model",
            "llama3",
            "--home",
            str(home),
        ],
    )
    assert config_res.exit_code == 0
    assert "Configured local model: llama3" in config_res.output

    config = load_model_config(home)
    assert config.adapter == "openai-compatible-loopback"
    assert config.endpoint == "http://127.0.0.1:11434"
    assert config.model == "llama3"

    disable_res = runner.invoke(app, ["model", "disable", "--home", str(home)])
    assert disable_res.exit_code == 0
    assert load_model_config(home).adapter == "disabled"


def test_model_cli_test_command(tmp_path: Path, mock_pong_server: str) -> None:
    home = tmp_path / "home"
    endpoint = mock_pong_server

    runner.invoke(
        app,
        [
            "model",
            "configure",
            "--endpoint",
            endpoint,
            "--model",
            "pong-model",
            "--home",
            str(home),
        ],
    )

    test_res = runner.invoke(app, ["model", "test", "--home", str(home)])
    assert test_res.exit_code == 0
    assert "Connectivity test succeeded" in test_res.output

    json_res = runner.invoke(app, ["model", "test", "--home", str(home), "--json"])
    assert json_res.exit_code == 0
    payload = json.loads(json_res.output)
    assert payload["status"] == "succeeded"
    assert payload["response"] == "PONG"


def test_model_config_loopback_validation(tmp_path: Path) -> None:
    home = tmp_path / "home"

    bad_https = runner.invoke(
        app,
        [
            "model",
            "configure",
            "--endpoint",
            "https://127.0.0.1:11434",
            "--model",
            "m",
            "--home",
            str(home),
        ],
    )
    assert bad_https.exit_code == 1
    assert "plain HTTP" in bad_https.output

    bad_remote = runner.invoke(
        app,
        [
            "model",
            "configure",
            "--endpoint",
            "http://api.openai.com:8080",
            "--model",
            "m",
            "--home",
            str(home),
        ],
    )
    assert bad_remote.exit_code == 1
    assert "loopback" in bad_remote.output

    bad_creds = runner.invoke(
        app,
        [
            "model",
            "configure",
            "--endpoint",
            "http://user:pass@127.0.0.1:11434",
            "--model",
            "m",
            "--home",
            str(home),
        ],
    )
    assert bad_creds.exit_code == 1
    assert "credentials" in bad_creds.output

    bad_path = runner.invoke(
        app,
        [
            "model",
            "configure",
            "--endpoint",
            "http://127.0.0.1:11434/v1",
            "--model",
            "m",
            "--home",
            str(home),
        ],
    )
    assert bad_path.exit_code == 1
    assert "server root" in bad_path.output
