/**
 * Express / TypeScript Control Plane & API Bridge (:3000) for Asset-Pipe.
 *
 * Bridges the institutional web console (:3000) with the Python/Polars Rust
 * reconciliation engine (`python -m src.interfaces.cli.pipeline_cli`),
 * enforcing Honest Zero Baseline ($0.00) and Zero-Hold Write Lock concurrency.
 */

import { spawnSync } from "child_process";
import express, { Express, Request, Response } from "express";
import path from "path";
import {
  DashboardRenderContext,
  EndpointConfigPayload,
  ExecutiveKpiPayload,
  LakeStorageMetricsPayload,
  renderExecutiveDashboardHtml,
} from "./dashboard_view";

export interface ServerBridgeOptions {
  pythonBin?: string;
  lakeDir?: string;
  duckdbPath?: string;
  port?: number;
}

const REPO_ROOT = path.resolve(__dirname, "..", "..", "..");

export function runPipelineCli(
  cliArgs: string[],
  options?: ServerBridgeOptions
): { exitCode: number; payload: Record<string, unknown> } {
  const pythonBin =
    options?.pythonBin ||
    process.env.PYTHON_BIN ||
    (process.platform === "win32" ? "python" : "python3");
  const lakeDir = options?.lakeDir || process.env.ASSET_PIPE_LAKE_DIR || "";
  const duckdbPath = options?.duckdbPath || process.env.DUCKDB_PATH || "";

  const fullArgs = ["-m", "src.interfaces.cli.pipeline_cli", ...cliArgs];
  if (lakeDir && !cliArgs.includes("--lake-dir")) {
    fullArgs.push("--lake-dir", lakeDir);
  }
  if (duckdbPath && !cliArgs.includes("--duckdb-path")) {
    fullArgs.push("--duckdb-path", duckdbPath);
  }

  const proc = spawnSync(pythonBin, fullArgs, {
    cwd: REPO_ROOT,
    encoding: "utf-8",
    maxBuffer: 16 * 1024 * 1024,
  });

  const stdoutText = (proc.stdout || "").trim();
  if (stdoutText) {
    try {
      const parsed = JSON.parse(stdoutText) as Record<string, unknown>;
      return { exitCode: proc.status ?? 0, payload: parsed };
    } catch {
      return {
        exitCode: proc.status ?? 1,
        payload: {
          status: "[CRITICAL]",
          code: "CLI_JSON_PARSE_ERROR",
          message: stdoutText.slice(0, 500),
        },
      };
    }
  }

  return {
    exitCode: proc.status ?? 1,
    payload: {
      status: "[CRITICAL]",
      code: "CLI_EXECUTION_ERROR",
      message: (proc.stderr || "Empty CLI output").trim(),
    },
  };
}

export function createApp(options?: ServerBridgeOptions): Express {
  const app = express();
  app.use(express.json({ limit: "2mb" }));

  let batchSequence = 0;
  const configuredPort = Number(options?.port || process.env.PORT || 3000);

  app.get("/api/v1/health", (_req: Request, res: Response) => {
    res.status(200).json({
      status: "[OK]",
      service: "asset-pipe-executive-console",
      port: configuredPort,
    });
  });

  app.get("/api/v1/status", (_req: Request, res: Response) => {
    const { exitCode, payload } = runPipelineCli(["--action", "status"], options);
    res.status(exitCode === 0 ? 200 : 500).json(payload);
  });

  app.get("/api/v1/positions", (req: Request, res: Response) => {
    const statusFilter = String(req.query.status_filter || "ALL");
    const search = String(req.query.search || "");
    const page = String(req.query.page || "1");
    const pageSize = String(req.query.page_size || "25");
    const sortBy = String(req.query.sort_by || "nav_break_abs_desc");

    const { exitCode, payload } = runPipelineCli(
      [
        "--action",
        "positions",
        "--status-filter",
        statusFilter,
        "--search",
        search,
        "--page",
        page,
        "--page-size",
        pageSize,
        "--sort-by",
        sortBy,
      ],
      options
    );
    res.status(exitCode === 0 ? 200 : 400).json(payload);
  });

  app.post("/api/v1/actions/ingest-valid", (req: Request, res: Response) => {
    batchSequence += 1;
    const count = Number(req.body?.count || 45);
    const seq = Number(req.body?.seq || batchSequence);
    const { exitCode, payload } = runPipelineCli(
      ["--action", "ingest-valid", "--count", String(count), "--seq", String(seq)],
      options
    );
    res.status(exitCode === 0 ? 200 : 400).json(payload);
  });

  app.post("/api/v1/actions/inject-corrupt", (req: Request, res: Response) => {
    batchSequence += 1;
    const breaksCount = Number(req.body?.breaks_count || 12);
    const corruptCount = Number(req.body?.corrupt_count || 6);
    const seq = Number(req.body?.seq || batchSequence);
    const { exitCode, payload } = runPipelineCli(
      [
        "--action",
        "inject-corrupt",
        "--breaks-count",
        String(breaksCount),
        "--corrupt-count",
        String(corruptCount),
        "--seq",
        String(seq),
      ],
      options
    );
    res.status(exitCode === 0 ? 200 : 400).json(payload);
  });

  app.post("/api/v1/actions/reconcile", (_req: Request, res: Response) => {
    const { exitCode, payload } = runPipelineCli(["--action", "reconcile"], options);
    res.status(exitCode === 0 ? 200 : 400).json(payload);
  });

  app.post("/api/v1/actions/reset", (_req: Request, res: Response) => {
    batchSequence = 0;
    const { exitCode, payload } = runPipelineCli(["--action", "reset"], options);
    res.status(exitCode === 0 ? 200 : 400).json(payload);
  });

  app.post("/api/v1/sql", (req: Request, res: Response) => {
    const query = String(req.body?.query || req.body?.sql || "");
    const { exitCode, payload } = runPipelineCli(
      ["--action", "sql", "--query", query],
      options
    );
    const httpCode = exitCode === 0 && payload.status === "[OK]" ? 200 : 400;
    res.status(httpCode).json(payload);
  });

  app.get("/", (_req: Request, res: Response) => {
    const { payload } = runPipelineCli(["--action", "status"], options);
    const ctx: DashboardRenderContext = {
      kpis: (payload.kpis as ExecutiveKpiPayload) || {
        total_positions: 0,
        validated_ibor_aum_usd: 0.0,
        custodian_abor_aum_usd: 0.0,
        reconciled_ok_count: 0,
        minor_tolerance_count: 0,
        nav_break_count: 0,
        schema_rejected_count: 0,
        total_breaks_and_rejects: 0,
        gross_nav_variance_usd: 0.0,
        net_nav_variance_usd: 0.0,
        max_break_bps: 0.0,
        warehouse_engine: "duckdb",
        last_updated_utc: "1970-01-01T00:00:00Z",
      },
      lake_metrics: (payload.lake_metrics as LakeStorageMetricsPayload) || {
        buckets: {},
        total_objects: 0,
        total_bytes: 0,
        compression_codec: "ZSTD",
      },
      endpoints: (payload.endpoints as EndpointConfigPayload) || {
        console_url: `http://localhost:${configuredPort}`,
        minio_console_url: "http://localhost:9001",
        s3_api_endpoint: "http://localhost:9000",
        sql_workbench_url: "http://localhost:4213",
        warehouse_engine: "duckdb",
      },
      s3ConsoleUrl:
        process.env.S3_PUBLIC_CONSOLE_URL ||
        (payload.endpoints as EndpointConfigPayload | undefined)?.minio_console_url ||
        "http://localhost:9001",
      sqlWorkbenchUrl:
        process.env.SQL_WORKBENCH_PUBLIC_URL ||
        (payload.endpoints as EndpointConfigPayload | undefined)?.sql_workbench_url ||
        "http://localhost:4213",
    };
    const html = renderExecutiveDashboardHtml(ctx);
    res.status(200).type("html").send(html);
  });

  return app;
}

if (require.main === module) {
  const port = Number(process.env.PORT || 3000);
  const app = createApp({ port });
  app.listen(port, "0.0.0.0", () => {
    process.stdout.write(
      `[OK] Asset-Pipe Executive Console listening on http://localhost:${port}\n`
    );
  });
}
