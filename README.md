# honba-data

Financial Data Lakehouse, DuckDB Fundamentals Store, Market Data Providers, and Service Layer for Honba Labs.

## Overview

`honba-data` manages all corporate financial statements, fundamental ratios, OHLCV time-series bars, and data coverage ledgers for Honba Labs. It is designed around the **Embedded-First, Microservice-Ready** pattern:
- **Embedded Mode**: Directly query local DuckDB (`fundamentals.duckdb`) and Parquet stores with zero network overhead.
- **Microservice Mode**: Run as a standalone daemon / microservice serving data over Arrow Flight, REST, or MCP tools to strategies, frontends, and AI agents.

## Key Capabilities

1. **DuckDB Company Fundamentals Lakehouse**:
   - Stores 5+ years of **Annual (`FY`) and Quarterly (`FQ`)** corporate filings for all Indian Large-Cap (Nifty 100) and Mid-Cap (Nifty Midcap 150) equities.
   - Comprehensive line items: Income Statements, Balance Sheets, and Cash Flow Statements.
   - Standardized fundamental ratios: ROCE %, ROE %, Operating Margins, Net Margins, Debt/Equity, Free Cash Flow.
2. **Strict Invariant**:
   - Never stores daily or intraday stock/equity price bars in DuckDB. Time-series price bars belong exclusively in immutable, append-only Parquet partitions.
   - Downloaded binary database and Parquet files are strictly excluded from git tracking (`.gitignore`).
3. **Automated Cross-Verification**:
   - Automated reconciliation test suite validating downloaded fundamentals against official BSE/NSE audited annual reports with **0.00% discrepancy**.
   - IndAS/GAAP accounting invariant audits ensuring mathematical identity consistency.
4. **Lakehouse Observability & Structured Ingestion Telemetry**:
   - Real-time `honba-data status` CLI inspection command.
   - Emits structured JSON telemetry payloads (`ingestion_completed`) tracking symbol attempted/succeeded/failed, rows written, delta days, and elapsed execution time.

---

## Concurrency Model & Runtime Isolation

`honba-data` is architected with strict concurrency and runtime boundaries:

1. **Batch/Offline Lakehouse Runtime**:
   `honba-data` operates as an offline, analytical lakehouse system. It is strictly decoupled from `honba-ya`'s low-latency pinned-thread actor runtime.
2. **Bounded Concurrency with Backpressure**:
   All data ingestion pipelines enforce bounded concurrency using `BoundedExecutor` or `asyncio.Semaphore`. Workloads are throttled with backpressure at submission time to avoid memory bloat and unbounded task queue accumulation.
3. **Prohibition of Uncoordinated ThreadPools in Live Paths**:
   Live actor threads (e.g. `honba-ya` trading actors) are isolated from lakehouse thread pool execution. Uncoordinated `ThreadPoolExecutor` creation within live trading paths is prohibited (`LiveActorConcurrencyViolation` guard) to prevent core thrashing, thread contention, and event-loop stalling.
4. **Provider Maturity Quarantining**:
   Official NSE/BSE corporate filings and official bhavcopy feeds are designated for production. Scraping drivers like `yfinance` are quarantined behind secondary adapter ports and marked `@pytest.mark.experimental` for dev/paper use only.

---

## Directory Structure

```text
honba-data/
├── pyproject.toml              # Package definition & CLI entrypoint
├── src/
│   └── honba_data/
│       ├── adapters/           # DuckDB & JSON ledger secondary adapters
│       ├── cli/                # honba-data Typer CLI (status, fundamentals, summary)
│       ├── concurrency.py      # Bounded concurrency & runtime isolation guards
│       ├── coverage/           # Coverage ledger & interval algebra
│       ├── domain/             # Entities, Value Objects, Domain Events
│       ├── live/               # Live drivers & quarantined pollers
│       ├── ports/              # Hexagonal interface contracts (DataService, Repos)
│       ├── providers/          # Fundamentals downloaders & parsers
│       ├── service/            # DataService implementation coordinating repos
│       ├── store/              # Storage facades (DuckDbFundamentalsStore, etc.)
│       └── telemetry.py        # Structured JSON ingestion telemetry schema & sink
├── tests/
│   ├── contract/               # Hexagonal DataService contract test suite
│   ├── integration/            # BSE/NSE ground truth cross-verification tests
│   └── unit/                   # Fast deterministic schema, algebra, & concurrency tests
├── scripts/
│   ├── download_fundamentals.py # Parallel downloader with terminal progress bar
│   └── verify_fundamentals.py   # Visual audited reconciliation scorecard
└── README.md
```

---

## Quickstart

### 1. Inspect Lakehouse Health & Regulatory Compliance (`honba-data status`)
```bash
# Render terminal tables: health, filing timeliness, provider error rates, and 0.00% discrepancy reconciliation
honba-data status

# Export structured health JSON for CI/monitoring
honba-data status --json
```

### 2. Download Large-Cap and Mid-Cap Fundamentals into DuckDB
```bash
# Using the CLI (bounded workers with backpressure)
honba-data fundamentals --universe all --workers 8

# Or using the downloader script
python scripts/download_fundamentals.py --universe all
```

### 3. Inspect Dataset Statistics
```bash
honba-data summary
```

### 4. Verify Against Audited Reports
```bash
python scripts/verify_fundamentals.py
```

### 5. Query via Python & DuckDB
```python
import duckdb

con = duckdb.connect("data/fundamentals.duckdb")

# Screener query: Low Debt + High ROCE + High ROE
df = con.execute("""
    SELECT c.symbol, c.company_name, c.cap_category, r.fiscal_date,
           round(r.roce_pct, 1) AS roce,
           round(r.roe_pct, 1) AS roe,
           round(r.debt_to_equity, 2) AS debt_eq
    FROM financial_ratios r
    JOIN companies c ON r.symbol = c.symbol
    WHERE r.period_type = 'FY'
      AND r.roce_pct >= 20.0
      AND r.roe_pct >= 20.0
      AND r.debt_to_equity <= 0.25
      AND r.fiscal_date >= '2025-01-01'
    ORDER BY r.roce_pct DESC;
""").df()

print(df)
```

---

## Testing & Quality Assurance

```bash
# Unit tests (including concurrency, telemetry, and CLI)
pytest tests/unit -v

# Hexagonal DataService Contract Tests
pytest tests/contract -v

# Audited Ground Truth Integration Tests
pytest tests/integration -v

# Lint & Format
ruff check .
ruff format --check .

# Static Type Checking
pyright
```
