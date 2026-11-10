/**
 * Institutional Financial Reconciliation Executive Console (:3000) View Renderer.
 *
 * Implements a Shadcn UI + Financial Power BI aesthetic with:
 * - Strict Zero-Emoji policy (Law 1) and pure CSS status dots (.status-dot-ok/warn/critical)
 * - Honest Zero Baseline ($0.00 and 0.00 bps) before batch ingestion or after Reset
 * - 5 interactive server-side filter KPI cards (ALL, RECONCILED_ONLY, BREAKS_ONLY)
 * - 4 live operational pipeline action buttons + 360-degree bridges to :9001 and :4213
 * - Contextual (i) popovers with explicit event.stopPropagation() & event.preventDefault()
 * - Server-side global search, pagination, and 1.5s non-intrusive auto-refresh loop
 */

export interface ExecutiveKpiPayload {
  total_positions: number;
  validated_ibor_aum_usd: number;
  custodian_abor_aum_usd: number;
  reconciled_ok_count: number;
  minor_tolerance_count: number;
  nav_break_count: number;
  schema_rejected_count: number;
  total_breaks_and_rejects: number;
  gross_nav_variance_usd: number;
  net_nav_variance_usd: number;
  max_break_bps: number;
  warehouse_engine: string;
  last_updated_utc: string;
}

export interface BucketMetricItem {
  objects: number;
  bytes: number;
}

export interface LakeStorageMetricsPayload {
  buckets: Record<string, BucketMetricItem>;
  total_objects: number;
  total_bytes: number;
  compression_codec: string;
}

export interface EndpointConfigPayload {
  console_url: string;
  minio_console_url: string;
  s3_api_endpoint: string;
  sql_workbench_url: string;
  warehouse_engine: string;
}

export interface DashboardRenderContext {
  kpis: ExecutiveKpiPayload;
  lake_metrics: LakeStorageMetricsPayload;
  endpoints?: EndpointConfigPayload;
  s3ConsoleUrl?: string;
  sqlWorkbenchUrl?: string;
}

function formatUsd(value: number): string {
  const safe = Number.isFinite(value) ? value : 0.0;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(safe);
}

function formatBps(value: number): string {
  const safe = Number.isFinite(value) ? value : 0.0;
  return `${safe.toFixed(2)} bps`;
}

export function renderExecutiveDashboardHtml(ctx: DashboardRenderContext): string {
  const kpis = ctx.kpis;
  const lake = ctx.lake_metrics || {
    buckets: {},
    total_objects: 0,
    total_bytes: 0,
    compression_codec: "ZSTD",
  };
  const s3ConsoleUrl =
    ctx.s3ConsoleUrl || ctx.endpoints?.minio_console_url || "http://localhost:9001";
  const sqlWorkbenchUrl =
    ctx.sqlWorkbenchUrl || ctx.endpoints?.sql_workbench_url || "http://localhost:4213";
  const engineName = (
    ctx.endpoints?.warehouse_engine ||
    kpis.warehouse_engine ||
    "duckdb"
  ).toUpperCase();

  const landingStat = lake.buckets?.["landing"] || { objects: 0, bytes: 0 };
  const validatedStat = lake.buckets?.["validated-parquet"] || { objects: 0, bytes: 0 };
  const quarantineStat = lake.buckets?.["quarantine"] || { objects: 0, bytes: 0 };

  const cleanTotal = kpis.reconciled_ok_count + kpis.minor_tolerance_count;
  const passRatePct =
    kpis.total_positions > 0
      ? ((cleanTotal / kpis.total_positions) * 100.0).toFixed(1)
      : "0.0";

  const iborAumStr = formatUsd(kpis.validated_ibor_aum_usd);
  const aborAumStr = formatUsd(kpis.custodian_abor_aum_usd);
  const grossVarStr = formatUsd(kpis.gross_nav_variance_usd);
  const netVarStr = formatUsd(kpis.net_nav_variance_usd);
  const maxBpsStr = formatBps(kpis.max_break_bps);

  const emptyBaselineRowHtml = `
    <tr id="honest-zero-row">
      <td colspan="13" class="empty-baseline-cell">
        <span class="status-dot status-dot-ok"></span>
        [OK] HONEST ZERO BASELINE ACTIVE — Warehouse and S3 Lake contain 0 records ($0.00). Trigger 'Ingest Valid Batch' or 'Inject Corrupt &amp; NAV Breaks' above.
      </td>
    </tr>`;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Asset-Pipe | Institutional Financial Reconciliation Console (:3000)</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet" />
  <style>
    :root {
      --bg-canvas: #09090b;
      --bg-surface: #121216;
      --bg-elevated: #18181f;
      --bg-hover: #1f1f29;
      --border-subtle: #27272a;
      --border-focus: #3b82f6;
      --text-primary: #f4f4f5;
      --text-secondary: #a1a1aa;
      --text-dim: #71717a;
      --emerald-ok: #10b981;
      --amber-warn: #f59e0b;
      --rose-critical: #f43f5e;
      --blue-brand: #3b82f6;
      --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-canvas);
      color: var(--text-primary);
      font-family: var(--font-sans);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      line-height: 1.45;
    }
    .mono-num {
      font-family: var(--font-mono);
      font-variant-numeric: tabular-nums;
    }
    .status-dot {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      margin-right: 6px;
      vertical-align: middle;
    }
    .status-dot-ok {
      background-color: var(--emerald-ok);
      box-shadow: 0 0 8px rgba(16, 185, 129, 0.55);
    }
    .status-dot-warn {
      background-color: var(--amber-warn);
      box-shadow: 0 0 8px rgba(245, 158, 11, 0.55);
    }
    .status-dot-critical {
      background-color: var(--rose-critical);
      box-shadow: 0 0 8px rgba(244, 63, 94, 0.65);
    }
    .top-header {
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border-subtle);
      padding: 0.85rem 1.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }
    .brand-cluster {
      display: flex;
      align-items: center;
      gap: 0.85rem;
    }
    .brand-logo {
      width: 34px;
      height: 34px;
      border-radius: 7px;
      background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
      border: 1px solid #334155;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .brand-title {
      font-size: 0.98rem;
      font-weight: 700;
      letter-spacing: -0.02em;
    }
    .brand-subtitle {
      font-size: 0.73rem;
      color: var(--text-secondary);
      font-family: var(--font-mono);
    }
    .header-controls {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.55rem;
    }
    .action-btn {
      position: relative;
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      background: var(--bg-elevated);
      color: var(--text-primary);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 0.48rem 0.85rem;
      font-size: 0.78rem;
      font-weight: 600;
      font-family: var(--font-sans);
      cursor: pointer;
      transition: all 0.14s ease;
    }
    .action-btn:hover {
      background: var(--bg-hover);
      border-color: var(--border-focus);
    }
    .action-btn-primary {
      background: rgba(16, 185, 129, 0.12);
      border-color: rgba(16, 185, 129, 0.45);
      color: #6ee7b7;
    }
    .action-btn-corrupt {
      background: rgba(244, 63, 94, 0.12);
      border-color: rgba(244, 63, 94, 0.45);
      color: #fda4af;
    }
    .action-btn-reconcile {
      background: rgba(59, 130, 246, 0.12);
      border-color: rgba(59, 130, 246, 0.45);
      color: #93c5fd;
    }
    .action-btn-reset {
      background: rgba(245, 158, 11, 0.1);
      border-color: rgba(245, 158, 11, 0.4);
      color: #fcd34d;
    }
    .external-bridge-link {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      background: #0f172a;
      color: #e2e8f0;
      border: 1px solid #334155;
      border-radius: 6px;
      padding: 0.46rem 0.8rem;
      font-size: 0.76rem;
      font-weight: 600;
      text-decoration: none;
      transition: border-color 0.15s ease;
    }
    .external-bridge-link:hover {
      border-color: var(--blue-brand);
      color: #ffffff;
    }
    .cred-pill {
      font-family: var(--font-mono);
      font-size: 0.68rem;
      background: rgba(15, 23, 42, 0.9);
      border: 1px solid #475569;
      padding: 0.1rem 0.38rem;
      border-radius: 4px;
      color: #94a3b8;
    }
    .info-trigger {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 17px;
      height: 17px;
      border-radius: 50%;
      border: 1px solid #52525b;
      background: rgba(24, 24, 31, 0.9);
      color: #d4d4d8;
      font-family: var(--font-mono);
      font-size: 0.66rem;
      font-weight: 700;
      cursor: pointer;
      margin-left: 4px;
      user-select: none;
    }
    .info-trigger:hover {
      border-color: var(--blue-brand);
      color: #ffffff;
      background: var(--blue-brand);
    }
    .tooltip-popover {
      display: none;
      position: absolute;
      top: calc(100% + 8px);
      left: 0;
      z-index: 90;
      width: 310px;
      background: #09090d;
      border: 1px solid #3f3f46;
      border-radius: 8px;
      padding: 0.75rem 0.85rem;
      box-shadow: 0 12px 28px rgba(0, 0, 0, 0.75);
      color: var(--text-primary);
      font-size: 0.74rem;
      font-weight: 400;
      text-align: left;
      line-height: 1.45;
    }
    .tooltip-popover.active { display: block; }
    .tooltip-formula {
      margin-top: 0.4rem;
      padding: 0.35rem 0.5rem;
      background: #14141b;
      border-left: 2px solid var(--blue-brand);
      font-family: var(--font-mono);
      font-size: 0.69rem;
      color: #93c5fd;
    }
    .workspace-container {
      max-width: 1640px;
      width: 100%;
      margin: 0 auto;
      padding: 1.15rem 1.5rem 2rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
      flex: 1;
    }
    .telemetry-strip {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      padding: 0.6rem 1rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.85rem;
      font-size: 0.76rem;
    }
    .telemetry-group {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.9rem;
    }
    .bucket-chip {
      position: relative;
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      font-family: var(--font-mono);
      font-size: 0.73rem;
      background: var(--bg-elevated);
      border: 1px solid var(--border-subtle);
      border-radius: 5px;
      padding: 0.24rem 0.55rem;
    }
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(220px, 1fr));
      gap: 0.9rem;
    }
    @media (max-width: 1280px) {
      .kpi-grid { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
    }
    .kpi-card {
      position: relative;
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      padding: 0.95rem 1.05rem;
      cursor: pointer;
      transition: border-color 0.15s ease, transform 0.12s ease, background 0.15s ease;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 124px;
    }
    .kpi-card:hover {
      border-color: var(--blue-brand);
      background: var(--bg-elevated);
      transform: translateY(-1px);
    }
    .kpi-card.active-filter {
      border-color: var(--blue-brand);
      box-shadow: 0 0 0 1px var(--blue-brand);
    }
    .kpi-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.75rem;
      font-weight: 600;
      color: var(--text-secondary);
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .kpi-primary-value {
      font-family: var(--font-mono);
      font-variant-numeric: tabular-nums;
      font-size: 1.48rem;
      font-weight: 700;
      margin: 0.45rem 0 0.35rem;
      color: var(--text-primary);
    }
    .kpi-subtext {
      font-size: 0.73rem;
      color: var(--text-secondary);
      font-family: var(--font-mono);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .filter-toolbar {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      padding: 0.75rem 1rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.85rem;
    }
    .search-box-wrap {
      flex: 1;
      min-width: 300px;
      display: flex;
      align-items: center;
      gap: 0.55rem;
    }
    .search-input {
      width: 100%;
      background: #050508;
      color: var(--text-primary);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 0.52rem 0.85rem;
      font-size: 0.81rem;
      font-family: var(--font-sans);
    }
    .search-input:focus {
      outline: none;
      border-color: var(--blue-brand);
    }
    .toolbar-right {
      display: flex;
      align-items: center;
      gap: 0.65rem;
      font-family: var(--font-mono);
      font-size: 0.75rem;
    }
    .table-panel {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      flex: 1;
    }
    .table-scroll {
      overflow-x: auto;
      max-height: 540px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.77rem;
    }
    th, td {
      padding: 0.58rem 0.72rem;
      text-align: left;
      border-bottom: 1px solid var(--border-subtle);
      white-space: nowrap;
    }
    th {
      background: var(--bg-elevated);
      color: var(--text-secondary);
      font-weight: 600;
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      position: sticky;
      top: 0;
      z-index: 10;
    }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }
    .empty-baseline-cell {
      text-align: center;
      padding: 2.4rem 1rem;
      font-family: var(--font-mono);
      font-size: 0.82rem;
      color: var(--text-secondary);
    }
    .badge-status {
      font-family: var(--font-mono);
      font-size: 0.69rem;
      font-weight: 600;
      padding: 0.18rem 0.48rem;
      border-radius: 4px;
      display: inline-flex;
      align-items: center;
    }
    .badge-reconciled {
      background: rgba(16, 185, 129, 0.12);
      color: #6ee7b7;
      border: 1px solid rgba(16, 185, 129, 0.35);
    }
    .badge-tolerance {
      background: rgba(245, 158, 11, 0.12);
      color: #fcd34d;
      border: 1px solid rgba(245, 158, 11, 0.35);
    }
    .badge-break {
      background: rgba(244, 63, 94, 0.14);
      color: #fda4af;
      border: 1px solid rgba(244, 63, 94, 0.45);
    }
    .pagination-bar {
      background: var(--bg-elevated);
      padding: 0.65rem 1rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-family: var(--font-mono);
      font-size: 0.75rem;
    }
    .page-btns { display: flex; gap: 0.4rem; }
    .page-btn {
      background: var(--bg-surface);
      color: var(--text-primary);
      border: 1px solid var(--border-subtle);
      border-radius: 5px;
      padding: 0.32rem 0.68rem;
      font-family: var(--font-mono);
      font-size: 0.73rem;
      cursor: pointer;
    }
    .page-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  </style>
</head>
<body>
  <header class="top-header">
    <div class="brand-cluster">
      <div class="brand-logo" aria-label="Asset-Pipe Columnar Cube Logo">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#60a5fa" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
          <polyline points="2 12 12 17 22 12"></polyline>
          <polyline points="2 17 12 22 22 17"></polyline>
        </svg>
      </div>
      <div>
        <div class="brand-title">ASSET-PIPE // INSTITUTIONAL RECONCILIATION CONSOLE</div>
        <div class="brand-subtitle">
          <span class="status-dot status-dot-ok"></span>[OK] POLARS RUST SIMD + PYDANTIC V2 + DUAL WAREHOUSE (${engineName})
        </div>
      </div>
    </div>

    <div class="header-controls">
      <button id="btn-ingest-valid" type="button" class="action-btn action-btn-primary" onclick="triggerPipelineAction('ingest-valid')">
        <span>Ingest Valid Batch</span>
        <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-act-valid')">(i)</span>
        <div id="tip-act-valid" class="tooltip-popover">
          <strong>Action 1: Ingest Clean Institutional Batch</strong><br/>
          Generates multi-currency positions with ISO 6166 Luhn Mod-10 ISINs, writes ZSTD Parquet to S3, and reconciles in Polars.
          <div class="tooltip-formula">POST /api/v1/actions/ingest-valid</div>
        </div>
      </button>

      <button id="btn-inject-corrupt" type="button" class="action-btn action-btn-corrupt" onclick="triggerPipelineAction('inject-corrupt')">
        <span>Inject Corrupt &amp; NAV Breaks</span>
        <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-act-corrupt')">(i)</span>
        <div id="tip-act-corrupt" class="tooltip-popover">
          <strong>Action 2: Forensic Stress &amp; Break Injection</strong><br/>
          Injects 3-factor NAV breaks (Quantity, Price, FX) and GX-01..GX-06 schema faults into S3 quarantine with SHA-256 manifests.
          <div class="tooltip-formula">POST /api/v1/actions/inject-corrupt</div>
        </div>
      </button>

      <button id="btn-reconcile" type="button" class="action-btn action-btn-reconcile" onclick="triggerPipelineAction('reconcile')">
        <span>Reconcile Lake (Polars)</span>
        <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-act-reconcile')">(i)</span>
        <div id="tip-act-reconcile" class="tooltip-popover">
          <strong>Action 3: Lazy Polars Lake Rescan</strong><br/>
          Executes pl.scan_parquet() with streaming evaluation over validated-parquet/ and upserts via Arrow Zero-Copy.
          <div class="tooltip-formula">POST /api/v1/actions/reconcile</div>
        </div>
      </button>

      <button id="btn-reset" type="button" class="action-btn action-btn-reset" onclick="triggerPipelineAction('reset')">
        <span>Reset to 0</span>
        <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-act-reset')">(i)</span>
        <div id="tip-act-reset" class="tooltip-popover">
          <strong>Action 4: Full Platform Reset to $0.00</strong><br/>
          Purges all 3 S3 buckets and truncates Warehouse tables back to the Honest Zero Baseline ($0.00).
          <div class="tooltip-formula">POST /api/v1/actions/reset</div>
        </div>
      </button>

      <a id="link-s3-lake" class="external-bridge-link" href="${s3ConsoleUrl}" target="_blank" rel="noopener noreferrer">
        <span>Open AWS S3 Lake (:9001)</span>
        <span class="cred-pill">admin / assetpipe2026</span>
      </a>

      <a id="link-sql-ui" class="external-bridge-link" href="${sqlWorkbenchUrl}" target="_blank" rel="noopener noreferrer">
        <span>Open Warehouse SQL UI (:4213)</span>
      </a>
    </div>
  </header>

  <main class="workspace-container">
    <section class="telemetry-strip" aria-label="Medallion S3 Data Lake and FinOps Telemetry">
      <div class="telemetry-group">
        <span><strong>S3 Medallion Lake (Parquet ZSTD):</strong></span>
        <span class="bucket-chip">
          landing/: <strong id="lake-landing-count">${landingStat.objects}</strong> obj (<span id="lake-landing-bytes">${landingStat.bytes}</span> B)
        </span>
        <span class="bucket-chip">
          validated-parquet/: <strong id="lake-validated-count">${validatedStat.objects}</strong> obj (<span id="lake-validated-bytes">${validatedStat.bytes}</span> B)
        </span>
        <span class="bucket-chip">
          quarantine/: <strong id="lake-quarantine-count">${quarantineStat.objects}</strong> obj (<span id="lake-quarantine-bytes">${quarantineStat.bytes}</span> B)
          <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-lake-forensics')">(i)</span>
          <div id="tip-lake-forensics" class="tooltip-popover">
            <strong>3-Bucket Medallion S3 Architecture</strong><br/>
            Clean rows are stored as ZSTD Parquet with Zone Map statistics; rejected rows are isolated in quarantine/ with a cryptographic SHA-256 .manifest.json sidecar.
          </div>
        </span>
      </div>
      <div class="telemetry-group">
        <span class="bucket-chip">
          <span class="status-dot status-dot-ok"></span>
          [FINOPS] Engine: <strong id="active-warehouse-engine">${engineName}</strong> | DuckDB :4213 Zero-Lock / Snowflake AUTO_SUSPEND=60s &amp; TIMEOUT=300s
          <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-finops-guard')">(i)</span>
          <div id="tip-finops-guard" class="tooltip-popover">
            <strong>Dual Warehouse &amp; FinOps Guardrails</strong><br/>
            DuckDB uses transient Zero-Hold Write Locks and read_only=True readers; Snowflake enforces X-Small AUTO_SUSPEND=60s, STATEMENT_TIMEOUT=300s, and 100% Resource Monitor circuit breakers.
          </div>
        </span>
      </div>
    </section>

    <section class="kpi-grid" aria-label="5 Interactive Executive KPI Filter Cards">
      <article id="card-total-positions" class="kpi-card active-filter" role="button" tabindex="0" onclick="setFilterAndSort('ALL', 'nav_break_abs_desc')">
        <div class="kpi-header">
          <span>1. Total Audited Positions</span>
          <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-kpi-1')">(i)</span>
        </div>
        <div id="tip-kpi-1" class="tooltip-popover">
          <strong>Total Audited Positions (IBOR + Quarantine)</strong><br/>
          Click this card to show all positions across the warehouse ordered by dollar break magnitude.
          <div class="tooltip-formula">COUNT(fact_reconciled_positions)</div>
        </div>
        <div id="kpi-val-total-positions" class="kpi-primary-value">${kpis.total_positions}</div>
        <div class="kpi-subtext">
          <span>Filter: ALL POSITIONS</span>
          <span id="kpi-sub-s3-objects">S3 Objects: ${lake.total_objects}</span>
        </div>
      </article>

      <article id="card-validated-aum" class="kpi-card" role="button" tabindex="0" onclick="setFilterAndSort('ALL', 'calculated_nav_desc')">
        <div class="kpi-header">
          <span>2. Validated IBOR AUM (USD)</span>
          <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-kpi-2')">(i)</span>
        </div>
        <div id="tip-kpi-2" class="tooltip-popover">
          <strong>Validated IBOR Net Asset Value (USD)</strong><br/>
          Independent valuation computed in Polars Rust from validated units, market prices, and FX rates.
          <div class="tooltip-formula">NAV_IBOR = SUM(Units * Price * FX_Rate)</div>
        </div>
        <div id="kpi-val-ibor-aum" class="kpi-primary-value">${iborAumStr}</div>
        <div class="kpi-subtext">
          <span>Custodian ABOR:</span>
          <span id="kpi-sub-abor-aum">${aborAumStr}</span>
        </div>
      </article>

      <article id="card-reconciled-ok" class="kpi-card" role="button" tabindex="0" onclick="setFilterAndSort('RECONCILED_ONLY', 'calculated_nav_desc')">
        <div class="kpi-header">
          <span><span class="status-dot status-dot-ok"></span>3. Reconciled Clean [OK]</span>
          <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-kpi-3')">(i)</span>
        </div>
        <div id="tip-kpi-3" class="tooltip-popover">
          <strong>Reconciled &amp; Minor Tolerance Positions</strong><br/>
          Click to filter only positions matching custodian valuations within institutional basis-point thresholds (&lt;= 5.0 bps).
          <div class="tooltip-formula">|Delta_NAV| &lt;= $0.01 OR Delta_bps &lt;= 5.0</div>
        </div>
        <div id="kpi-val-reconciled-ok" class="kpi-primary-value">${cleanTotal}</div>
        <div class="kpi-subtext">
          <span id="kpi-sub-minor-tol">Exact: ${kpis.reconciled_ok_count} | Tol: ${kpis.minor_tolerance_count}</span>
          <span id="kpi-sub-pass-rate">${passRatePct}% Pass</span>
        </div>
      </article>

      <article id="card-nav-breaks" class="kpi-card" role="button" tabindex="0" onclick="setFilterAndSort('BREAKS_ONLY', 'nav_break_abs_desc')">
        <div class="kpi-header">
          <span><span class="status-dot status-dot-critical"></span>4. NAV &amp; Schema Breaks [CRITICAL]</span>
          <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-kpi-4')">(i)</span>
        </div>
        <div id="tip-kpi-4" class="tooltip-popover">
          <strong>Unified Accounting Breaks &amp; Quarantine Triage</strong><br/>
          Click to isolate ONLY material NAV breaks (&gt; 5.0 bps) and schema-rejected records from v_nav_and_schema_breaks_triage.
          <div class="tooltip-formula">Filter: BREAKS_ONLY (NAV_BREAK + SCHEMA_REJECTED)</div>
        </div>
        <div id="kpi-val-total-breaks" class="kpi-primary-value">${kpis.total_breaks_and_rejects}</div>
        <div class="kpi-subtext">
          <span id="kpi-sub-nav-breaks">[NAV-BREAK]: ${kpis.nav_break_count}</span>
          <span id="kpi-sub-schema-rejects">Quarantine: ${kpis.schema_rejected_count}</span>
        </div>
      </article>

      <article id="card-gross-variance" class="kpi-card" role="button" tabindex="0" onclick="setFilterAndSort('BREAKS_ONLY', 'nav_break_bps_desc')">
        <div class="kpi-header">
          <span><span class="status-dot status-dot-warn"></span>5. Gross NAV Variance ($ / bps)</span>
          <span class="info-trigger" role="button" tabindex="0" onclick="toggleTooltip(event, 'tip-kpi-5')">(i)</span>
        </div>
        <div id="tip-kpi-5" class="tooltip-popover">
          <strong>Absolute Gross Variance vs. Net Variance</strong><br/>
          Sums |NAV_IBOR - NAV_ABOR| so positive and negative breaks never cancel each other out. Click to sort breaks by basis points.
          <div class="tooltip-formula">Gross = SUM(|Delta_NAV|) | bps = (|Delta| / NAV) * 10000</div>
        </div>
        <div id="kpi-val-gross-variance" class="kpi-primary-value">${grossVarStr}</div>
        <div class="kpi-subtext">
          <span id="kpi-sub-net-variance">Net: ${netVarStr}</span>
          <span id="kpi-sub-max-bps">Max: ${maxBpsStr}</span>
        </div>
      </article>
    </section>

    <section class="filter-toolbar" aria-label="Server-Side Search and Live Refresh Controls">
      <div class="search-box-wrap">
        <input
          id="search-input"
          type="search"
          class="search-input"
          placeholder="Search across entire Warehouse by Position ID, ISIN (e.g. US0378331005), Portfolio, Currency, or Break Driver..."
          aria-label="Global Server-Side Search"
        />
      </div>
      <div class="toolbar-right">
        <span id="active-filter-indicator">Active View: [ALL] | Sort: [nav_break_abs_desc]</span>
        <button id="btn-toggle-autorefresh" type="button" class="page-btn" onclick="toggleAutoRefresh()">
          Auto-Refresh (1.5s): [ON]
        </button>
        <span id="action-telemetry-status">[OK] Ready</span>
      </div>
    </section>

    <section class="table-panel" aria-label="Audited Positions and Forensic Break Attribution Grid">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Position ID</th>
              <th>Portfolio</th>
              <th>ISIN (ISO 6166)</th>
              <th>CCY</th>
              <th>Units</th>
              <th>Market Price</th>
              <th>FX Rate</th>
              <th>IBOR Calc NAV ($)</th>
              <th>Custodian ABOR NAV ($)</th>
              <th>Delta Break ($)</th>
              <th>Severity (bps)</th>
              <th>Primary Driver (Q / P / FX)</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody id="positions-table-body">
            ${emptyBaselineRowHtml}
          </tbody>
        </table>
      </div>
      <div class="pagination-bar">
        <div id="pagination-summary">Page 1 of 1 (Total 0 rows)</div>
        <div class="page-btns">
          <button id="btn-page-first" type="button" class="page-btn" onclick="goToPage(1)">First</button>
          <button id="btn-page-prev" type="button" class="page-btn" onclick="changePage(-1)">Prev</button>
          <button id="btn-page-next" type="button" class="page-btn" onclick="changePage(1)">Next</button>
          <button id="btn-page-last" type="button" class="page-btn" onclick="goToLastPage()">Last</button>
        </div>
      </div>
    </section>
  </main>

  <script>
    var currentStatusFilter = 'ALL';
    var currentSortBy = 'nav_break_abs_desc';
    var currentSearchQuery = '';
    var currentPage = 1;
    var currentPageSize = 25;
    var currentTotalPages = 1;
    var autoRefreshEnabled = true;
    var searchDebounceTimer = null;
    var isActionInFlight = false;

    function fmtUsd(val) {
      var num = Number(val);
      if (!Number.isFinite(num)) { num = 0.0; }
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
      }).format(num);
    }

    function fmtNum(val, decimals) {
      var num = Number(val);
      if (!Number.isFinite(num)) { num = 0.0; }
      return num.toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
      });
    }

    function toggleTooltip(event, tooltipId) {
      event.stopPropagation();
      event.preventDefault();
      var allPopovers = document.querySelectorAll('.tooltip-popover');
      var target = document.getElementById(tooltipId);
      var wasActive = target && target.classList.contains('active');
      for (var i = 0; i < allPopovers.length; i++) {
        allPopovers[i].classList.remove('active');
      }
      if (target && !wasActive) {
        target.classList.add('active');
      }
    }

    document.addEventListener('click', function() {
      var allPopovers = document.querySelectorAll('.tooltip-popover');
      for (var i = 0; i < allPopovers.length; i++) {
        allPopovers[i].classList.remove('active');
      }
    });

    function setFilterAndSort(statusFilter, sortBy) {
      currentStatusFilter = statusFilter;
      currentSortBy = sortBy;
      currentPage = 1;
      var indicator = document.getElementById('active-filter-indicator');
      if (indicator) {
        indicator.textContent = 'Active View: [' + currentStatusFilter + '] | Sort: [' + currentSortBy + ']';
      }
      refreshPositionsTable();
    }

    function toggleAutoRefresh() {
      autoRefreshEnabled = !autoRefreshEnabled;
      var btn = document.getElementById('btn-toggle-autorefresh');
      if (btn) {
        btn.textContent = 'Auto-Refresh (1.5s): [' + (autoRefreshEnabled ? 'ON' : 'PAUSED') + ']';
      }
    }

    function goToPage(pageNum) {
      currentPage = Math.max(1, Math.min(pageNum, currentTotalPages));
      refreshPositionsTable();
    }

    function changePage(delta) {
      goToPage(currentPage + delta);
    }

    function goToLastPage() {
      goToPage(currentTotalPages);
    }

    function renderStatusBadge(status) {
      var st = String(status || 'RECONCILED');
      if (st === 'RECONCILED') {
        return '<span class="badge-status badge-reconciled"><span class="status-dot status-dot-ok"></span>[OK] RECONCILED</span>';
      }
      if (st === 'MINOR_TOLERANCE') {
        return '<span class="badge-status badge-tolerance"><span class="status-dot status-dot-warn"></span>[WARN] MINOR_TOL</span>';
      }
      if (st === 'SCHEMA_REJECTED') {
        return '<span class="badge-status badge-break"><span class="status-dot status-dot-critical"></span>[CRITICAL] QUARANTINE</span>';
      }
      return '<span class="badge-status badge-break"><span class="status-dot status-dot-critical"></span>[NAV-BREAK] ' + st + '</span>';
    }

    function renderDriverCell(row) {
      var driver = String(row.break_driver || 'NONE');
      if (driver === 'NONE') {
        return '<span class="mono-num">NONE ($0.00)</span>';
      }
      if (driver === 'SCHEMA_CORRUPTION') {
        var rule = String(row.rule_id || 'CONTRACT_GATE');
        return '<span class="mono-num" title="' + String(row.diagnostic_message || '') + '">' + driver + ' [' + rule + ']</span>';
      }
      var qEff = fmtUsd(row.qty_effect_usd || 0);
      var pEff = fmtUsd(row.price_effect_usd || 0);
      var fxEff = fmtUsd(row.fx_effect_usd || 0);
      return '<span class="mono-num">' + driver + ' (Q:' + qEff + ' | P:' + pEff + ' | FX:' + fxEff + ')</span>';
    }

    function updateKpiDom(kpis, lake) {
      if (!kpis) { return; }
      var cleanCount = Number(kpis.reconciled_ok_count || 0) + Number(kpis.minor_tolerance_count || 0);
      var totalPos = Number(kpis.total_positions || 0);
      var passPct = totalPos > 0 ? ((cleanCount / totalPos) * 100.0).toFixed(1) : '0.0';

      var elTotal = document.getElementById('kpi-val-total-positions');
      if (elTotal) { elTotal.textContent = String(totalPos); }
      var elIbor = document.getElementById('kpi-val-ibor-aum');
      if (elIbor) { elIbor.textContent = fmtUsd(kpis.validated_ibor_aum_usd); }
      var elAbor = document.getElementById('kpi-sub-abor-aum');
      if (elAbor) { elAbor.textContent = fmtUsd(kpis.custodian_abor_aum_usd); }
      var elClean = document.getElementById('kpi-val-reconciled-ok');
      if (elClean) { elClean.textContent = String(cleanCount); }
      var elMinor = document.getElementById('kpi-sub-minor-tol');
      if (elMinor) {
        elMinor.textContent = 'Exact: ' + (kpis.reconciled_ok_count || 0) + ' | Tol: ' + (kpis.minor_tolerance_count || 0);
      }
      var elPass = document.getElementById('kpi-sub-pass-rate');
      if (elPass) { elPass.textContent = passPct + '% Pass'; }
      var elBreaks = document.getElementById('kpi-val-total-breaks');
      if (elBreaks) { elBreaks.textContent = String(kpis.total_breaks_and_rejects || 0); }
      var elNavBrk = document.getElementById('kpi-sub-nav-breaks');
      if (elNavBrk) { elNavBrk.textContent = '[NAV-BREAK]: ' + (kpis.nav_break_count || 0); }
      var elRej = document.getElementById('kpi-sub-schema-rejects');
      if (elRej) { elRej.textContent = 'Quarantine: ' + (kpis.schema_rejected_count || 0); }
      var elGross = document.getElementById('kpi-val-gross-variance');
      if (elGross) { elGross.textContent = fmtUsd(kpis.gross_nav_variance_usd); }
      var elNet = document.getElementById('kpi-sub-net-variance');
      if (elNet) { elNet.textContent = 'Net: ' + fmtUsd(kpis.net_nav_variance_usd); }
      var elBps = document.getElementById('kpi-sub-max-bps');
      if (elBps) { elBps.textContent = 'Max: ' + fmtNum(kpis.max_break_bps, 2) + ' bps'; }

      if (lake && lake.buckets) {
        var bLand = lake.buckets['landing'] || { objects: 0, bytes: 0 };
        var bVal = lake.buckets['validated-parquet'] || { objects: 0, bytes: 0 };
        var bQuar = lake.buckets['quarantine'] || { objects: 0, bytes: 0 };
        var s3Sub = document.getElementById('kpi-sub-s3-objects');
        if (s3Sub) { s3Sub.textContent = 'S3 Objects: ' + (lake.total_objects || 0); }
        var lCnt = document.getElementById('lake-landing-count');
        if (lCnt) { lCnt.textContent = String(bLand.objects); }
        var lByt = document.getElementById('lake-landing-bytes');
        if (lByt) { lByt.textContent = String(bLand.bytes); }
        var vCnt = document.getElementById('lake-validated-count');
        if (vCnt) { vCnt.textContent = String(bVal.objects); }
        var vByt = document.getElementById('lake-validated-bytes');
        if (vByt) { vByt.textContent = String(bVal.bytes); }
        var qCnt = document.getElementById('lake-quarantine-count');
        if (qCnt) { qCnt.textContent = String(bQuar.objects); }
        var qByt = document.getElementById('lake-quarantine-bytes');
        if (qByt) { qByt.textContent = String(bQuar.bytes); }
      }
    }

    async function refreshStatusAndKpis() {
      try {
        var res = await fetch('/api/v1/status');
        if (!res.ok) { return; }
        var data = await res.json();
        updateKpiDom(data.kpis, data.lake_metrics);
      } catch (_err) {
        return;
      }
    }

    async function refreshPositionsTable() {
      try {
        var qs = (
          'status_filter=' + encodeURIComponent(currentStatusFilter) +
          '&search=' + encodeURIComponent(currentSearchQuery) +
          '&page=' + encodeURIComponent(String(currentPage)) +
          '&page_size=' + encodeURIComponent(String(currentPageSize)) +
          '&sort_by=' + encodeURIComponent(currentSortBy)
        );
        var res = await fetch('/api/v1/positions?' + qs);
        if (!res.ok) { return; }
        var data = await res.json();
        var rows = data.rows || [];
        currentTotalPages = Number(data.total_pages || 1);
        currentPage = Number(data.page || 1);
        var totalRows = Number(data.total_rows || 0);

        var summary = document.getElementById('pagination-summary');
        if (summary) {
          summary.textContent = 'Page ' + currentPage + ' of ' + currentTotalPages + ' (Total ' + totalRows + ' rows)';
        }

        var tbody = document.getElementById('positions-table-body');
        if (!tbody) { return; }

        if (rows.length === 0) {
          tbody.innerHTML = (
            '<tr id="honest-zero-row"><td colspan="13" class="empty-baseline-cell">' +
            '<span class="status-dot status-dot-ok"></span>' +
            '[OK] HONEST ZERO BASELINE ACTIVE — Warehouse and S3 Lake contain 0 records ($0.00). ' +
            "Trigger 'Ingest Valid Batch' or 'Inject Corrupt &amp; NAV Breaks' above." +
            '</td></tr>'
          );
          return;
        }

        var htmlRows = [];
        for (var i = 0; i < rows.length; i++) {
          var r = rows[i];
          htmlRows.push(
            '<tr>' +
            '<td class="mono-num">' + (r.position_id || '') + '</td>' +
            '<td class="mono-num">' + (r.portfolio_id || '') + '</td>' +
            '<td class="mono-num">' + (r.isin || '') + '</td>' +
            '<td class="mono-num">' + (r.currency || '') + '</td>' +
            '<td class="mono-num">' + fmtNum(r.units, 2) + '</td>' +
            '<td class="mono-num">' + fmtNum(r.price, 4) + '</td>' +
            '<td class="mono-num">' + fmtNum(r.fx_rate, 4) + '</td>' +
            '<td class="mono-num">' + fmtUsd(r.calculated_nav) + '</td>' +
            '<td class="mono-num">' + fmtUsd(r.custodian_reported_nav) + '</td>' +
            '<td class="mono-num">' + fmtUsd(r.nav_break_signed) + '</td>' +
            '<td class="mono-num">' + fmtNum(r.nav_break_bps, 2) + ' bps</td>' +
            '<td>' + renderDriverCell(r) + '</td>' +
            '<td>' + renderStatusBadge(r.reconciliation_status) + '</td>' +
            '</tr>'
          );
        }
        tbody.innerHTML = htmlRows.join('');
      } catch (_err) {
        return;
      }
    }

    async function triggerPipelineAction(actionName) {
      if (isActionInFlight) { return; }
      isActionInFlight = true;
      var statusEl = document.getElementById('action-telemetry-status');
      if (statusEl) {
        statusEl.textContent = '[OK] Executing ' + actionName + '...';
      }
      try {
        var res = await fetch('/api/v1/actions/' + actionName, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        var data = await res.json();
        if (data.kpis) {
          updateKpiDom(data.kpis, data.lake_metrics);
        }
        if (actionName === 'reset') {
          currentStatusFilter = 'ALL';
          currentPage = 1;
        }
        await refreshPositionsTable();
        if (statusEl) {
          statusEl.textContent = (data.status || '[OK]') + ' Action ' + actionName + ' completed';
        }
      } catch (_err) {
        if (statusEl) {
          statusEl.textContent = '[CRITICAL] Action ' + actionName + ' failed';
        }
      } finally {
        isActionInFlight = false;
      }
    }

    var searchInputEl = document.getElementById('search-input');
    if (searchInputEl) {
      searchInputEl.addEventListener('input', function(e) {
        var val = e.target && typeof e.target.value === 'string' ? e.target.value : '';
        if (searchDebounceTimer) { clearTimeout(searchDebounceTimer); }
        searchDebounceTimer = setTimeout(function() {
          currentSearchQuery = val.trim();
          currentPage = 1;
          refreshPositionsTable();
        }, 180);
      });
    }

    setInterval(function() {
      if (!autoRefreshEnabled || isActionInFlight) { return; }
      refreshStatusAndKpis();
      refreshPositionsTable();
    }, 1500);

    window.addEventListener('DOMContentLoaded', function() {
      refreshStatusAndKpis();
      refreshPositionsTable();
    });
  </script>
</body>
</html>`;
}
