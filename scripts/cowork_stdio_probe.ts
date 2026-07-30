#!/usr/bin/env -S npx tsx
/** Exercise Archiv through CoWork-OS's actual StdioTransport implementation. */

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

interface Arguments {
  coworkRoot: string;
  outputDir: string;
  mode: "pinned" | "current";
}

interface Stage {
  stage: string;
  status: "passed" | "failed" | "not_exercised";
  owner_if_failed:
    | "archiv"
    | "mcp_transport"
    | "cowork_integration"
    | "model_provider"
    | "workbench_orchestration";
  detail: string;
}

interface ToolResult {
  content?: Array<{ type: string; text?: string }>;
  structuredContent?: Record<string, unknown> | null;
  isError?: boolean;
}

function parseArguments(argv: string[]): Arguments {
  const values = new Map<string, string>();
  for (let index = 0; index < argv.length; index += 2) {
    values.set(argv[index], argv[index + 1] || "");
  }
  const coworkRoot = values.get("--cowork-root") || "";
  const outputDir = values.get("--output-dir") || "";
  const mode = values.get("--mode") || "";
  if (!coworkRoot || !outputDir || (mode !== "pinned" && mode !== "current")) {
    throw new Error(
      "Usage: cowork_stdio_probe.ts --cowork-root <path> --output-dir <path> --mode pinned|current",
    );
  }
  return { coworkRoot, outputDir, mode };
}

function structured(result: ToolResult): Record<string, any> {
  if (result.isError) {
    throw new Error("MCP tool returned isError=true");
  }
  if (result.structuredContent) {
    return result.structuredContent as Record<string, any>;
  }
  const text = result.content?.find((item) => item.type === "text")?.text;
  if (!text) {
    throw new Error("MCP tool returned no structured content or JSON text");
  }
  return JSON.parse(text) as Record<string, any>;
}

async function main(): Promise<void> {
  const args = parseArguments(process.argv.slice(2));
  const outputDir = path.resolve(args.outputDir);
  const plan = JSON.parse(
    await fs.readFile(path.join(outputDir, "regression-plan.json"), "utf8"),
  ) as Record<string, any>;
  const archivHome = String(plan.archiv_home);
  const transportPath = path.join(
    path.resolve(args.coworkRoot),
    "src/electron/mcp/client/transports/StdioTransport.ts",
  );
  const transportModule = (await import(pathToFileURL(transportPath).href)) as {
    StdioTransport: new (config: Record<string, unknown>) => any;
  };
  const stages: Stage[] = [];
  const runIds: string[] = [];
  let transport: any = null;

  async function stage<T>(
    name: string,
    owner: Stage["owner_if_failed"],
    operation: () => Promise<T>,
  ): Promise<T> {
    try {
      const value = await operation();
      stages.push({ stage: name, status: "passed", owner_if_failed: owner, detail: "passed" });
      return value;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      stages.push({ stage: name, status: "failed", owner_if_failed: owner, detail });
      throw error;
    }
  }

  try {
    transport = new transportModule.StdioTransport({
      id: `archiv-${args.mode}`,
      name: `Archiv ${args.mode}`,
      enabled: true,
      transport: "stdio",
      command: "archiv-mcp",
      args: [],
      env: { ARCHIV_HOME: archivHome },
      cwd: outputDir,
      connectionTimeout: 30000,
      requestTimeout: 120000,
    });
    transport.onMessage(() => undefined);
    transport.onClose(() => undefined);
    transport.onError(() => undefined);

    await stage("cowork_stdio_spawn", "cowork_integration", async () => transport.connect());
    await stage("mcp_initialize", "mcp_transport", async () => {
      const initialized = await transport.sendRequest("initialize", {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "Archiv CoWork regression", version: "1.0.0" },
      });
      if (!initialized?.serverInfo?.name) {
        throw new Error("initialize response did not contain serverInfo");
      }
      await transport.send({ jsonrpc: "2.0", method: "notifications/initialized" });
    });

    const tools = await stage("tool_discovery", "mcp_transport", async () => {
      const result = await transport.sendRequest("tools/list");
      const names = new Set((result.tools || []).map((tool: any) => tool.name));
      const expected = [
        "archiv_ingest",
        "archiv_search",
        "archiv_read_source",
        "archiv_generate_docx",
        "archiv_verify_artifact",
        "archiv_get_run_evidence",
      ];
      for (const name of expected) {
        if (!names.has(name)) throw new Error(`missing Archiv tool: ${name}`);
      }
      return result.tools;
    });

    const searchEnvelope = await stage("archiv_search", "archiv", async () => {
      const result = (await transport.sendRequest("tools/call", {
        name: "archiv_search",
        arguments: { query: "MARKER", limit: 10 },
      })) as ToolResult;
      const envelope = structured(result);
      if (envelope.status !== "succeeded") throw new Error("search envelope did not succeed");
      runIds.push(String(envelope.run_id));
      const results = envelope.result?.results;
      if (!Array.isArray(results) || results.length !== 3) {
        throw new Error(`expected three search results, received ${results?.length ?? "none"}`);
      }
      return envelope;
    });

    const citation = searchEnvelope.result.results[0].citation;
    await stage("archiv_read_source", "archiv", async () => {
      const result = (await transport.sendRequest("tools/call", {
        name: "archiv_read_source",
        arguments: { citation },
      })) as ToolResult;
      const envelope = structured(result);
      runIds.push(String(envelope.run_id));
      if (envelope.result?.excerpt !== searchEnvelope.result.results[0].text) {
        throw new Error("source excerpt did not match the search result");
      }
    });

    const validName = `cowork-${args.mode}-valid.docx`;
    const validReport = await stage("archiv_generate_docx", "archiv", async () => {
      const result = (await transport.sendRequest("tools/call", {
        name: "archiv_generate_docx",
        arguments: {
          query: "MARKER",
          output_name: validName,
          max_sources: 3,
          render: false,
        },
      })) as ToolResult;
      const envelope = structured(result);
      runIds.push(String(envelope.run_id));
      if (envelope.result?.report?.validation?.valid !== true) {
        throw new Error("generated report was not independently valid");
      }
      return envelope.result.report;
    });

    await stage("archiv_verify_artifact", "archiv", async () => {
      const result = (await transport.sendRequest("tools/call", {
        name: "archiv_verify_artifact",
        arguments: { output_name: validName, render: false },
      })) as ToolResult;
      const envelope = structured(result);
      runIds.push(String(envelope.run_id));
      if (envelope.result?.validation?.valid !== true) {
        throw new Error("artifact verification did not return valid=true");
      }
    });

    await stage("run_evidence_outside_workbench", "archiv", async () => {
      const result = (await transport.sendRequest("tools/call", {
        name: "archiv_get_run_evidence",
        arguments: { run_id: searchEnvelope.run_id },
      })) as ToolResult;
      const envelope = structured(result);
      runIds.push(String(envelope.run_id));
      const target = envelope.result?.evidence?.target_result;
      if (target?.run_id !== searchEnvelope.run_id || target?.status !== "succeeded") {
        throw new Error("run evidence did not resolve the search success record");
      }
    });

    await stage("tool_error_propagation", "mcp_transport", async () => {
      const result = (await transport.sendRequest("tools/call", {
        name: "archiv_ingest",
        arguments: { source_path: "relative.txt" },
      })) as ToolResult;
      if (result.isError !== true || result.structuredContent != null) {
        throw new Error("invalid ingest was not returned as an unstructured MCP tool error");
      }
    });

    await stage("failed_validation_cannot_succeed", "cowork_integration", async () => {
      const tamperedName = `cowork-${args.mode}-tampered.docx`;
      const generated = (await transport.sendRequest("tools/call", {
        name: "archiv_generate_docx",
        arguments: {
          query: "MARKER",
          output_name: tamperedName,
          max_sources: 3,
          render: false,
        },
      })) as ToolResult;
      const generatedEnvelope = structured(generated);
      runIds.push(String(generatedEnvelope.run_id));
      const reportPath = String(generatedEnvelope.result.report.docx_path);
      await fs.appendFile(reportPath, Buffer.from("tampered-by-cowork-regression"));
      const failed = (await transport.sendRequest("tools/call", {
        name: "archiv_verify_artifact",
        arguments: { output_name: tamperedName, render: false },
      })) as ToolResult;
      if (failed.isError !== true || failed.structuredContent != null) {
        throw new Error("tampered report was represented as structured success");
      }
    });

    stages.push({
      stage: "model_provider",
      status: "not_exercised",
      owner_if_failed: "model_provider",
      detail: "No model is required to prove the pinned MCP boundary.",
    });
    stages.push({
      stage: "workbench_orchestration",
      status: "not_exercised",
      owner_if_failed: "workbench_orchestration",
      detail: "The transport/tool contract is tested independently of an LLM task loop.",
    });

    await fs.writeFile(
      path.join(outputDir, "cowork-probe.json"),
      JSON.stringify(
        {
          schema_version: 1,
          mode: args.mode,
          tools: tools.map((tool: any) => tool.name).sort(),
          valid_report_path: validReport.docx_path,
          valid_manifest_path: validReport.manifest_path,
          valid_validation_path: validReport.validation_path,
          run_ids: runIds,
          stages,
        },
        null,
        2,
      ) + "\n",
    );
  } catch (error) {
    await fs.writeFile(
      path.join(outputDir, "cowork-probe.json"),
      JSON.stringify({ schema_version: 1, mode: args.mode, run_ids: runIds, stages }, null, 2) +
        "\n",
    );
    throw error;
  } finally {
    if (transport) await transport.disconnect().catch(() => undefined);
  }
}

await main();
