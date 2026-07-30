from __future__ import annotations

import sys
from pathlib import Path
from typing import cast

import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp_support import prepare_mcp_archive

from archiv.mcp_contracts import McpRunEnvelope
from archiv.mcp_server import mcp

EXPECTED_TOOLS = {
    "archiv_ingest",
    "archiv_search",
    "archiv_read_source",
    "archiv_generate_docx",
    "archiv_get_run_evidence",
    "archiv_verify_artifact",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_in_memory_mcp_contract_and_tool_errors(
    ingestion_corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "archiv-home"
    prepare_mcp_archive(ingestion_corpus, home)
    monkeypatch.setenv("ARCHIV_HOME", str(home))

    async with Client(mcp, raise_exceptions=True) as client:
        listed = await client.list_tools()
        tools = {tool.name: tool for tool in listed.tools}
        assert set(tools) == EXPECTED_TOOLS
        search_schema = tools["archiv_search"].input_schema
        properties = cast(dict[str, object], search_schema["properties"])
        assert "schema_version" in properties
        assert "query" in cast(list[str], search_schema["required"])

        result = await client.call_tool("archiv_search", {"query": "MARKER", "limit": 10})
        assert result.is_error is False
        assert result.structured_content is not None
        envelope = McpRunEnvelope.model_validate(result.structured_content)
        assert envelope.tool == "archiv_search"
        assert envelope.status == "succeeded"

        invalid = await client.call_tool("archiv_ingest", {"source_path": "relative.txt"})
        assert invalid.is_error is True
        assert invalid.structured_content is None


@pytest.mark.anyio
async def test_real_stdio_subprocess_lists_and_calls_tools(
    ingestion_corpus: Path,
    tmp_path: Path,
) -> None:
    home = tmp_path / "archiv-home"
    prepare_mcp_archive(ingestion_corpus, home)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "archiv.mcp_server"],
        env={"ARCHIV_HOME": str(home)},
    )

    async with Client(stdio_client(parameters), raise_exceptions=True) as client:
        listed = await client.list_tools()
        assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS
        result = await client.call_tool("archiv_search", {"query": "MARKER", "limit": 10})
        assert result.is_error is False
        assert result.structured_content is not None
        envelope = McpRunEnvelope.model_validate(result.structured_content)
        results = cast(list[dict[str, object]], envelope.result["results"])
        assert len(results) == 3
