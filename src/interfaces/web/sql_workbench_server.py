"""Zero-Lock Read-Only Warehouse SQL Workbench Server (:4213).

Provides an institutional Snowsight/Shadcn-inspired dark web SQL explorer over
DuckDB (read_only=True Snapshot Isolation) and Snowflake Cloud with 5 one-click
forensic templates and regex anti-mutation guardrails.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from src.domain.exceptions import AssetPipeDomainError
from src.infrastructure.config import AssetPipeConfig
from src.infrastructure.warehouse_factory import create_warehouse_adapter

PRECONFIGURED_INSTITUTIONAL_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "1. Executive KPIs (Honest Zero Check)",
        "SELECT * FROM v_executive_reconciliation_kpis;",
    ),
    (
        "2. NAV & Schema Breaks Triage",
        "SELECT * FROM v_nav_and_schema_breaks_triage ORDER BY nav_break_abs DESC LIMIT 50;",
    ),
    (
        "3. Portfolio AUM Rollup (Gross vs Net)",
        "SELECT * FROM v_portfolio_aum_rollup ORDER BY gross_fund_break_usd DESC;",
    ),
    (
        "4. Reconciled Positions Fact Table",
        "SELECT position_id, portfolio_id, isin, currency, units, price, fx_rate, "
        "calculated_nav, custodian_reported_nav, nav_break_abs, nav_break_bps, "
        "reconciliation_status, break_driver "
        "FROM fact_reconciled_positions ORDER BY nav_break_abs DESC LIMIT 50;",
    ),
    (
        "5. Quarantine Forensic Audit Log",
        "SELECT batch_id, position_id, portfolio_id, isin, rule_id, violation_code, "
        "diagnostic_message, quarantined_at_utc "
        "FROM fact_quarantine_audit_log LIMIT 50;",
    ),
)


def execute_workbench_query(
    sql: str,
    config: AssetPipeConfig | None = None,
) -> tuple[int, dict[str, object]]:
    """Execute a read-only SQL query and return (http_status_code, response_dict)."""
    cfg = config or AssetPipeConfig.from_env()
    warehouse = create_warehouse_adapter(cfg)
    try:
        result = warehouse.execute_readonly_sql(sql)
        return 200, {
            "status": "[OK]",
            "warehouse_engine": cfg.warehouse_engine,
            **result,
        }
    except AssetPipeDomainError as exc:
        return 400, {
            "status": "[CRITICAL]",
            "warehouse_engine": cfg.warehouse_engine,
            **exc.to_dict(),
        }
    except Exception as exc:  # noqa: BLE001
        return 400, {
            "status": "[CRITICAL]",
            "error_type": exc.__class__.__name__,
            "code": "SQL_WORKBENCH_EXECUTION_ERROR",
            "message": f"[CRITICAL] {exc}",
        }


def render_sql_workbench_html(config: AssetPipeConfig | None = None) -> str:
    """Render the dark-mode Shadcn/Snowsight SQL Workbench HTML5 interface."""
    cfg = config or AssetPipeConfig.from_env()
    engine_label = (
        "DuckDB Zero-Lock OLAP"
        if cfg.warehouse_engine == "duckdb"
        else "Snowflake Enterprise Cloud"
    )
    console_url = f"http://localhost:{cfg.web_port}"
    minio_url = cfg.s3_public_console_url

    buttons_html = "\n".join(
        f'<button type="button" class="preset-btn" data-sql="{sql}">{label}</button>'
        for label, sql in PRECONFIGURED_INSTITUTIONAL_QUERIES
    )
    default_sql = PRECONFIGURED_INSTITUTIONAL_QUERIES[0][1]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Asset-Pipe | Warehouse SQL Workbench (:{cfg.sql_ui_port})</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link
    href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
    rel="stylesheet"
  />
  <link
    href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap"
    rel="stylesheet"
  />
  <style>
    :root {{
      --bg-primary: #09090b;
      --bg-card: #121216;
      --bg-elevated: #18181f;
      --border: #27272a;
      --text-main: #f4f4f5;
      --text-muted: #a1a1aa;
      --accent-blue: #3b82f6;
      --accent-emerald: #10b981;
      --accent-rose: #f43f5e;
      --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-mono: 'JetBrains Mono', monospace;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg-primary);
      color: var(--text-main);
      font-family: var(--font-sans);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }}
    header {{
      border-bottom: 1px solid var(--border);
      background: var(--bg-card);
      padding: 0.9rem 1.5rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }}
    .brand-group {{ display: flex; align-items: center; gap: 0.85rem; }}
    .brand-title {{ font-size: 1rem; font-weight: 700; letter-spacing: -0.02em; }}
    .badge {{
      font-family: var(--font-mono);
      font-size: 0.72rem;
      padding: 0.25rem 0.6rem;
      border-radius: 4px;
      border: 1px solid var(--border);
      background: var(--bg-elevated);
    }}
    .badge-ok {{
      color: var(--accent-emerald);
      border-color: rgba(16, 185, 129, 0.35);
      background: rgba(16, 185, 129, 0.08);
    }}
    .nav-links {{ display: flex; gap: 0.65rem; align-items: center; }}
    .nav-link {{
      color: var(--text-main);
      text-decoration: none;
      font-size: 0.8rem;
      font-weight: 500;
      padding: 0.45rem 0.85rem;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--bg-elevated);
      transition: border-color 0.15s ease;
    }}
    .nav-link:hover {{ border-color: var(--accent-blue); }}
    main {{
      max-width: 1480px;
      width: 100%;
      margin: 0 auto;
      padding: 1.25rem 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
      flex: 1;
    }}
    .preset-bar {{
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 0.85rem 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.6rem;
    }}
    .preset-label {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: var(--text-muted);
      font-weight: 600;
    }}
    .preset-buttons {{ display: flex; flex-wrap: wrap; gap: 0.5rem; }}
    .preset-btn {{
      background: var(--bg-elevated);
      color: var(--text-main);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.45rem 0.75rem;
      font-size: 0.78rem;
      font-family: var(--font-sans);
      cursor: pointer;
    }}
    .preset-btn:hover {{ border-color: var(--accent-blue); color: #93c5fd; }}
    .editor-card {{
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }}
    textarea#sql-input {{
      width: 100%;
      min-height: 115px;
      background: #050507;
      color: #e4e4e7;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.8rem;
      font-family: var(--font-mono);
      font-size: 0.85rem;
      resize: vertical;
    }}
    .editor-actions {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
    }}
    .run-btn {{
      background: var(--accent-blue);
      color: #ffffff;
      border: none;
      border-radius: 6px;
      padding: 0.55rem 1.1rem;
      font-weight: 600;
      font-size: 0.82rem;
      cursor: pointer;
    }}
    .telemetry {{
      font-family: var(--font-mono);
      font-size: 0.78rem;
      color: var(--text-muted);
    }}
    .results-card {{
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      flex: 1;
      display: flex;
      flex-direction: column;
    }}
    .table-wrap {{ overflow-x: auto; max-height: 540px; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-family: var(--font-mono);
      font-size: 0.77rem;
    }}
    th, td {{
      padding: 0.55rem 0.75rem;
      text-align: left;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }}
    th {{
      background: var(--bg-elevated);
      color: var(--text-muted);
      font-weight: 600;
      position: sticky;
      top: 0;
    }}
    .error-banner {{
      padding: 1rem;
      color: var(--accent-rose);
      font-family: var(--font-mono);
      font-size: 0.82rem;
      display: none;
    }}
  </style>
</head>
<body>
  <header>
    <div class="brand-group">
      <span class="brand-title">ASSET-PIPE // WAREHOUSE SQL WORKBENCH (:{cfg.sql_ui_port})</span>
      <span class="badge">{engine_label}</span>
      <span class="badge badge-ok">[OK] READ-ONLY SNAPSHOT ISOLATION</span>
    </div>
    <div class="nav-links">
      <a class="nav-link" href="{minio_url}" target="_blank" rel="noopener">
        MinIO S3 Console (:9001)
      </a>
      <a class="nav-link" href="{console_url}">Back to Executive Console (:3000)</a>
    </div>
  </header>
  <main>
    <section class="preset-bar">
      <div class="preset-label">Institutional 1-Click Read-Only Audit Templates</div>
      <div class="preset-buttons">
        {buttons_html}
      </div>
    </section>
    <section class="editor-card">
      <textarea id="sql-input" spellcheck="false">{default_sql}</textarea>
      <div class="editor-actions">
        <button id="run-query-btn" type="button" class="run-btn">
          Run Read-Only Query (Ctrl+Enter)
        </button>
        <div id="query-telemetry" class="telemetry">
          Status: [OK] Ready | Rows: 0 | Latency: 0.00 ms
        </div>
      </div>
    </section>
    <section class="results-card">
      <div id="error-box" class="error-banner"></div>
      <div class="table-wrap">
        <table id="results-table">
          <thead id="results-head"></thead>
          <tbody id="results-body"></tbody>
        </table>
      </div>
    </section>
  </main>
  <script>
    const sqlInput = document.getElementById('sql-input');
    const runBtn = document.getElementById('run-query-btn');
    const telemetry = document.getElementById('query-telemetry');
    const errorBox = document.getElementById('error-box');
    const thead = document.getElementById('results-head');
    const tbody = document.getElementById('results-body');

    async function runQuery() {{
      const sql = sqlInput.value.trim();
      errorBox.style.display = 'none';
      telemetry.textContent = 'Status: Executing read-only snapshot query...';
      try {{
        const res = await fetch('/api/query', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ sql }})
        }});
        const data = await res.json();
        if (!res.ok || data.status === '[CRITICAL]') {{
          thead.innerHTML = '';
          tbody.innerHTML = '';
          errorBox.style.display = 'block';
          const st = data.status || '[CRITICAL]';
          const cd = data.code || '';
          const msg = data.message || 'Query rejected';
          errorBox.textContent = st + ' ' + cd + ': ' + msg;
          telemetry.textContent = 'Status: [CRITICAL] Rejected by Read-Only Guard';
          return;
        }}
        const cols = data.columns || [];
        const rows = data.rows || [];
        thead.innerHTML = '<tr>' + cols.map(c => '<th>' + c + '</th>').join('') + '</tr>';
        tbody.innerHTML = rows.map(r => {{
          const cells = cols.map(c => '<td>' + (r[c] ?? 'NULL') + '</td>').join('');
          return '<tr>' + cells + '</tr>';
        }}).join('');
        telemetry.textContent = (
          'Status: ' + data.status +
          ' | Rows: ' + data.row_count +
          ' | Latency: ' + data.execution_ms + ' ms'
        );
      }} catch (err) {{
        errorBox.style.display = 'block';
        errorBox.textContent = '[CRITICAL] Network or parser error: ' + String(err);
      }}
    }}

    document.querySelectorAll('.preset-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        sqlInput.value = btn.getAttribute('data-sql') || '';
        runQuery();
      }});
    }});
    runBtn.addEventListener('click', runQuery);
    sqlInput.addEventListener('keydown', (e) => {{
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {{
        e.preventDefault();
        runQuery();
      }}
    }});
    window.addEventListener('DOMContentLoaded', runQuery);
  </script>
</body>
</html>"""


class SqlWorkbenchRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving the :4213 SQL Workbench UI and JSON API."""

    config: AssetPipeConfig = AssetPipeConfig.from_env()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Suppress noisy default stderr access logs during interactive polling."""
        return

    def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        """Handle GET /api/health and GET / HTML workbench view."""
        if self.path.startswith("/api/health"):
            self._send_json(
                200,
                {
                    "status": "[OK]",
                    "service": "asset-pipe-sql-workbench",
                    "port": self.config.sql_ui_port,
                    "warehouse_engine": self.config.warehouse_engine,
                },
            )
            return
        html_bytes = render_sql_workbench_html(self.config).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_bytes)))
        self.end_headers()
        self.wfile.write(html_bytes)

    def do_POST(self) -> None:  # noqa: N802
        """Handle POST /api/query for read-only analytical SQL execution."""
        if not self.path.startswith("/api/query"):
            self._send_json(404, {"status": "[CRITICAL]", "message": "Endpoint not found"})
            return
        content_len = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_len).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            self._send_json(400, {"status": "[CRITICAL]", "message": "Invalid JSON body"})
            return
        sql_query = str(payload.get("sql", ""))
        code, response = execute_workbench_query(sql_query, self.config)
        self._send_json(code, response)


def run_sql_workbench_server(config: AssetPipeConfig | None = None) -> None:
    """Start the ThreadingHTTPServer on 0.0.0.0:4213 (or configured SQL_UI_PORT)."""
    cfg = config or AssetPipeConfig.from_env()
    SqlWorkbenchRequestHandler.config = cfg
    server = ThreadingHTTPServer(("0.0.0.0", cfg.sql_ui_port), SqlWorkbenchRequestHandler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    run_sql_workbench_server()
