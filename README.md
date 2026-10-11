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

---

## Directory Structure

```text
honba-data/
├── pyproject.toml              # Package definition & CLI entrypoint
├── src/
│   └── honba_data/
│       ├── store/              # DuckDbFundamentalsStore & ParquetBarStore
│       ├── providers/          # fundamentals downloader, NSE Bhavcopy, yfinance
│       ├── coverage/           # Coverage ledger & interval algebra
│       ├── service/            # DataService port & contract
│       └── cli/                # honba-data Typer CLI
├── tests/
│   ├── unit/                   # Fast deterministic schema & store tests
│   └── integration/            # BSE/NSE ground truth cross-verification tests
├── scripts/
│   ├── download_fundamentals.py # Parallel downloader with terminal progress bar
│   └── verify_fundamentals.py   # Visual audited reconciliation scorecard
└── README.md
```

---

## Quickstart

### 1. Download Large-Cap and Mid-Cap Fundamentals into DuckDB
```bash
# Using the CLI
honba-data fundamentals --universe all --workers 8

# Or using the downloader script
python scripts/download_fundamentals.py --universe all
```

### 2. Inspect Dataset Statistics
```bash
honba-data summary
```

### 3. Verify Against Audited Reports
```bash
python scripts/verify_fundamentals.py
```

### 4. Query via Python & DuckDB
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
# Unit tests
pytest tests/unit -v

# Audited Ground Truth Integration Tests
pytest tests/integration -v

# Lint & Format
ruff check .
ruff format --check .
```
