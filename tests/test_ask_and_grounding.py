from __future__ import annotations

import json
import socket
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest
from typer.testing import CliRunner

from archiv.cli import app
from archiv.contracts import RunStatus
from archiv.grounding import parse_and_validate_grounded_response, run_grounded_ask
from archiv.model_adapter import ModelConfig, save_model_config
from archiv.sample_vault import create_sample_vault

runner = CliRunner()


class MockModelHandler(BaseHTTPRequestHandler):
    response_body: dict[str, object] = {}
    response_status: int = 200
    delay_seconds: float = 0.0

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        payload = json.dumps(self.response_body).encode("utf-8")
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def mock_model_server():
    server = HTTPServer(("127.0.0.1", 0), MockModelHandler)
    thread = Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    port = server.server_port
    endpoint = f"http://127.0.0.1:{port}"
    yield endpoint, MockModelHandler
    server.shutdown()
    server.server_close()


def _set_model(tmp_home: Path, endpoint: str, model_name: str = "test-model") -> None:
    config = ModelConfig(
        adapter="openai-compatible-loopback",
        endpoint=endpoint,
        model=model_name,
        timeout_seconds=5,
    )
    save_model_config(config, tmp_home)


def test_ask_with_disabled_model(tmp_path: Path) -> None:
    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])

    result = run_grounded_ask("unique fixture marker", home=home)
    assert result.status == RunStatus.BLOCKED_BY_POLICY
    assert "disabled" in result.errors[0]


def test_ask_successful_grounded_answer(tmp_path: Path, mock_model_server) -> None:
    endpoint, handler = mock_model_server
    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    _set_model(home, endpoint)

    handler.response_body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "schema_version": "1",
                            "paragraphs": [
                                {
                                    "paragraph_id": "PAR-1",
                                    "text": "The operational finding indicates immutable originals remain unchanged.",
                                    "citation_ids": ["CIT-1"],
                                }
                            ],
                            "claims": [
                                {
                                    "claim_id": "CLM-1",
                                    "statement": "Independent validators determine whether work succeeded.",
                                    "citation_ids": ["CIT-1", "CIT-2"],
                                }
                            ],
                            "insufficient_evidence": [],
                            "contradictions": [],
                        }
                    )
                }
            }
        ]
    }

    run_res = run_grounded_ask("unique fixture marker", home=home)
    assert run_res.status == RunStatus.SUCCEEDED
    assert run_res.grounded_response is not None
    assert len(run_res.retrieved_citations) >= 1
    assert run_res.grounded_response["paragraphs"][0]["citation_ids"] == ["CIT-1"]


def test_ask_unknown_citation_rejection(tmp_path: Path, mock_model_server) -> None:
    endpoint, handler = mock_model_server
    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    _set_model(home, endpoint)

    handler.response_body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "schema_version": "1",
                            "paragraphs": [
                                {
                                    "paragraph_id": "PAR-1",
                                    "text": "Fabricated claim.",
                                    "citation_ids": ["CIT-99"],
                                }
                            ],
                            "claims": [],
                            "insufficient_evidence": [],
                            "contradictions": [],
                        }
                    )
                }
            }
        ]
    }

    run_res = run_grounded_ask("unique fixture marker", home=home)
    assert run_res.status == RunStatus.PARTIALLY_PRODUCED_BUT_INVALID
    assert any("unknown or un-retrieved source" in err for err in run_res.errors)


def test_ask_malformed_json_response(tmp_path: Path, mock_model_server) -> None:
    endpoint, handler = mock_model_server
    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    _set_model(home, endpoint)

    handler.response_body = {
        "choices": [{"message": {"content": "This is plain text, not JSON."}}]
    }

    run_res = run_grounded_ask("unique fixture marker", home=home)
    assert run_res.status == RunStatus.PARTIALLY_PRODUCED_BUT_INVALID
    assert any("malformed model JSON" in err for err in run_res.errors)


def test_ask_model_timeout_and_unreachable(tmp_path: Path) -> None:
    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    _set_model(home, f"http://127.0.0.1:{port}")

    run_res = run_grounded_ask("unique fixture marker", home=home)
    assert run_res.status == RunStatus.FAILED
    assert any("model request failed" in err for err in run_res.errors)


def test_ask_insufficient_evidence_and_contradictions(tmp_path: Path, mock_model_server) -> None:
    endpoint, handler = mock_model_server
    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    _set_model(home, endpoint)

    handler.response_body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "schema_version": "1",
                            "paragraphs": [],
                            "claims": [],
                            "insufficient_evidence": [
                                "No budget information present in evidence."
                            ],
                            "contradictions": ["Operations text conflicts with decision text."],
                        }
                    )
                }
            }
        ]
    }

    run_res = run_grounded_ask("unique fixture marker", home=home)
    assert run_res.status == RunStatus.SUCCEEDED
    assert run_res.grounded_response["insufficient_evidence"] == [
        "No budget information present in evidence."
    ]
    assert run_res.grounded_response["contradictions"] == [
        "Operations text conflicts with decision text."
    ]


def test_ask_cli_readable_and_json_output(tmp_path: Path, mock_model_server) -> None:
    endpoint, handler = mock_model_server
    home = tmp_path / "home"
    corpus = tmp_path / "corpus"
    create_sample_vault(corpus)
    runner.invoke(app, ["add", str(corpus), "--home", str(home)])
    _set_model(home, endpoint)

    handler.response_body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "schema_version": "1",
                            "paragraphs": [
                                {
                                    "paragraph_id": "PAR-1",
                                    "text": "All findings verified.",
                                    "citation_ids": ["CIT-1"],
                                }
                            ],
                            "claims": [],
                            "insufficient_evidence": [],
                            "contradictions": [],
                        }
                    )
                }
            }
        ]
    }

    res_cli = runner.invoke(app, ["ask", "unique fixture marker", "--home", str(home)])
    assert res_cli.exit_code == 0
    assert "All findings verified." in res_cli.output
    assert "Verified Sources:" in res_cli.output

    res_json = runner.invoke(
        app, ["ask", "unique fixture marker", "--home", str(home), "--json"]
    )
    assert res_json.exit_code == 0
    parsed = json.loads(res_json.output)
    assert parsed["status"] == "succeeded"
    assert parsed["grounded_response"]["paragraphs"][0]["text"] == "All findings verified."
