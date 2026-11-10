/**
 * Vitest Suite for Executive Console (:3000) SSR, V8 Script Compilation, and API Bridge.
 *
 * Enforces Honest Zero Baseline ($0.00), Zero-Emoji policy (Law 1), zero dead links,
 * V8 syntax validation via new Function(scriptContent), 5 interactive KPI filter
 * cards, 4 action buttons, :9001/:4213 bridges, event.stopPropagation() tooltips,
 * and live Express /api/v1/health + /api/v1/status integration.
 */

import fs from "fs";
import http from "http";
import os from "os";
import path from "path";
import { describe, expect, it } from "vitest";
import {
  DashboardRenderContext,
  renderExecutiveDashboardHtml,
} from "../../src/interfaces/web/dashboard_view";
import { createApp } from "../../src/interfaces/web/server";

const EMOJI_REGEX = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u;

function createZeroBaselineContext(): DashboardRenderContext {
  return {
    kpis: {
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
    lake_metrics: {
      buckets: {
        landing: { objects: 0, bytes: 0 },
        "validated-parquet": { objects: 0, bytes: 0 },
        quarantine: { objects: 0, bytes: 0 },
      },
      total_objects: 0,
      total_bytes: 0,
      compression_codec: "ZSTD",
    },
    endpoints: {
      console_url: "http://localhost:3000",
      minio_console_url: "http://localhost:9001",
      s3_api_endpoint: "http://localhost:9000",
      sql_workbench_url: "http://localhost:4213",
      warehouse_engine: "duckdb",
    },
    s3ConsoleUrl: "http://localhost:9001",
    sqlWorkbenchUrl: "http://localhost:4213",
  };
}

describe("Asset-Pipe :3000 Executive Console & API Bridge Integrity", () => {
  it("renders Honest Zero Baseline ($0.00), zero emojis, and zero dead href='#' links", () => {
    const html = renderExecutiveDashboardHtml(createZeroBaselineContext());

    expect(html.startsWith("<!DOCTYPE html>")).toBe(true);
    expect(EMOJI_REGEX.test(html)).toBe(false);
    expect(html).not.toContain('href="#"');
    expect(html).toContain("$0.00");
    expect(html).toContain("0.00 bps");
    expect(html).toContain(
      "[OK] HONEST ZERO BASELINE ACTIVE — Warehouse and S3 Lake contain 0 records ($0.00)."
    );
  });

  it("compiles embedded browser <script> cleanly in V8 via new Function(scriptContent)", () => {
    const html = renderExecutiveDashboardHtml(createZeroBaselineContext());
    const scriptMatch = html.match(/<script>([\s\S]*?)<\/script>/);
    expect(scriptMatch).not.toBeNull();

    const scriptContent = scriptMatch ? scriptMatch[1] : "";
    expect(scriptContent.length).toBeGreaterThan(500);
    expect(() => new Function(scriptContent)).not.toThrow();
  });

  it("contains all 5 KPI filter cards, 4 action buttons, :9001/:4213 bridges, and event.stopPropagation()", () => {
    const html = renderExecutiveDashboardHtml(createZeroBaselineContext());

    const requiredCardIds = [
      'id="card-total-positions"',
      'id="card-validated-aum"',
      'id="card-reconciled-ok"',
      'id="card-nav-breaks"',
      'id="card-gross-variance"',
    ];
    for (const cardId of requiredCardIds) {
      expect(html).toContain(cardId);
    }

    const requiredButtonIds = [
      'id="btn-ingest-valid"',
      'id="btn-inject-corrupt"',
      'id="btn-reconcile"',
      'id="btn-reset"',
    ];
    for (const btnId of requiredButtonIds) {
      expect(html).toContain(btnId);
    }

    expect(html).toContain('id="link-s3-lake"');
    expect(html).toContain("http://localhost:9001");
    expect(html).toContain("admin / assetpipe2026");
    expect(html).toContain('id="link-sql-ui"');
    expect(html).toContain("http://localhost:4213");

    expect(html).toContain("event.stopPropagation()");
    expect(html).toContain("event.preventDefault()");
    expect(html).toContain("setFilterAndSort('BREAKS_ONLY'");
    expect(html).toContain('id="search-input"');
  });

  it("serves /api/v1/health and /api/v1/status with Honest Zero Baseline via createApp()", async () => {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "asset-pipe-web-test-"));
    const app = createApp({
      lakeDir: path.join(tmpDir, "s3_lake"),
      duckdbPath: path.join(tmpDir, "web_test.duckdb"),
      port: 3000,
    });

    const server = http.createServer(app);
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const addr = server.address();
    const port = typeof addr === "object" && addr ? addr.port : 3000;

    try {
      const healthRes = await fetch(`http://127.0.0.1:${port}/api/v1/health`);
      expect(healthRes.status).toBe(200);
      const healthJson = (await healthRes.json()) as Record<string, unknown>;
      expect(healthJson.status).toBe("[OK]");
      expect(EMOJI_REGEX.test(JSON.stringify(healthJson))).toBe(false);

      const statusRes = await fetch(`http://127.0.0.1:${port}/api/v1/status`);
      expect(statusRes.status).toBe(200);
      const statusJson = (await statusRes.json()) as {
        status: string;
        kpis: { total_positions: number; validated_ibor_aum_usd: number };
      };
      expect(statusJson.status).toBe("[OK]");
      expect(statusJson.kpis.total_positions).toBe(0);
      expect(statusJson.kpis.validated_ibor_aum_usd).toBe(0.0);
      expect(EMOJI_REGEX.test(JSON.stringify(statusJson))).toBe(false);
    } finally {
      await new Promise<void>((resolve) => server.close(() => resolve()));
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  });
});
