# Master Plan: 本場・資 honba-data (Financial Lakehouse & Fundamentals Engine)

**Target Repository**: `honba-data` (Hexagonal Lakehouse, Fundamentals Engine, and Storage Layer)  
**Status**: ACTIVE REFACTORING & ARCHITECTURAL CONVERGENCE  
**Priority**: P0 / P1 / P2

---

## 0. Executive Summary & Architectural Audit

A comprehensive forensic audit of the `honba-data` codebase reveals substantial functional capability (e.g., DuckDB fundamentals store, corporate actions accounting adjustments, SEBI regulatory audit trail, Bhavcopy loaders, yfinance drivers, and automated data quality audits). However, significant architectural debt, protocol divergence, and contract fragmentation must be resolved to align `honba-data` with Honba Labs' production standards:

1. **Dependency Inversion Violation**: `honba-data` imports directly from `honba.domain` without explicit pinning or shared package boundary definition.
2. **Missing `DataService` Implementation**: The `README.md` and documentation advertise a high-level `DataService` interface providing fundamentals, ratios, bars, and coverage queries, but no such protocol or concrete class is implemented in `src/honba_data/`.
3. **Tactical DDD Violations**: `DuckDbFundamentalsStore` contains inline SQL queries and direct disk access within transaction scripts rather than adhering to repository interfaces (`FundamentalsRepository`, `CoverageRepository`).
4. **Lakehouse Divergence**: `honba-data` maintains an ad-hoc Parquet store with `f64` bars and JSON ledger, which conflicts with `honba-me`'s canonical Hive-partitioned lakehouse (`Decimal128(18,4)`).
5. **Lack of Concurrency Boundaries & Observability**: Downloads rely on uncoordinated `ThreadPoolExecutor` instances with no backpressure or isolation from live actor runtimes.

---

## 1. Architectural Scope & Bounded Contexts

`honba-data` constitutes the **Financial Lakehouse & Historical Fundamentals Bounded Context** within the Honba ecosystem.

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Honba Ecosystem Architecture                          │
├───────────────────────────────────────┬─────────────────────────────────────────┤
│              honba-me                 │               honba-data                │
│    (Market Execution & Real-Time)     │    (Lakehouse & Fundamentals Engine)    │
├───────────────────────────────────────┼─────────────────────────────────────────┤
│ - Order Matching Engine (C++ / Rust)  │ - Corporate Financials (DuckDB)         │
│ - Market Data Feedhandlers & Ticks    │ - Balance Sheets, P&L, Cash Flows       │
│ - Real-Time L2/L3 Book Ingestion      │ - Computed Ratios (ROCE, ROE, Margins)  │
│ - Canonical Hive Parquet Lakehouse    │ - Corporate Actions & Split Adjustments │
│ - Tick-to-Trade Microsecond Latency   │ - SEBI Regulatory Audit Trail           │
│ - Pinned-thread Actor Runtime         │ - Coverage Ledgers & Data Quality Audits│
└───────────────────────────────────────┴─────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                honba-ya / honba-tamashii / honba-strategies                     │
│  - Algorithmic Trading Engines & Multi-Horizon Strategy Screeners               │
│  - Fundamental + Technical Confluence Alpha Models                             │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Separation of Concerns: `honba-data` vs `honba-me`

To eliminate reconciliation drift and incompatible storage engines:
1. **`honba-data` Scope (Fundamentals & Corporate Filings)**:
   - Primary custodian of corporate filings: Balance Sheets, Income Statements, Cash Flow Statements.
   - Computes standardized fundamental ratios: ROCE, ROE, Operating Margins, Net Margins, Debt/Equity, Free Cash Flow.
   - Historical corporate actions (splits, bonuses, dividends, symbol changes) in DuckDB.
   - SEBI audit cross-verification against audited annual reports (0.00% tolerance).
   - Point-in-time (PIT) as-of query engine preventing look-ahead bias.
2. **`honba-me` Scope (Market Data Engine)**:
   - Primary custodian of real-time ticks, order book depth (L2/5-depth), and OHLCV time-series bars.
   - Canonical lakehouse stored in Hive-partitioned Parquet files with `Decimal128(18,4)` financial price purity.
   - Daily Bhavcopy market data ingestion and trading calendar validation.
3. **Convergence Rule**:
   - `honba-data` will **not** maintain a competing, divergent Parquet market data store using `f64`.
   - Any historical bar query requested through `honba-data`'s service layer will delegate to `honba-me`'s `BarReader` / Parquet dataset or use unified Arrow IPC contracts.

---

## 2. Tactical DDD Architecture & Module Hierarchy

`honba-data` will transition from transaction-script stores to a clean Hexagonal (Ports & Adapters) Domain-Driven Design:

```text
honba-data/
├── pyproject.toml                     # Dependencies, toolchains, entrypoints
├── README.md                          # Architecture, contracts, quickstart
├── plan.md                            # Subsystem master execution plan
└── src/
    └── honba_data/
        ├── domain/                    # Pure Domain Layer (Entities, Value Objects, Events)
        │   ├── __init__.py
        │   ├── models.py              # Company, FinancialStatement, FinancialRatios, LineItem
        │   ├── coverage.py            # DateInterval, CoverageRecord, QualityIssue
        │   └── events.py              # FundamentalsIngested, LedgerUpdated, IngestionFailed
        ├── ports/                     # Inbound & Outbound Interfaces (Hexagonal Ports)
        │   ├── __init__.py
        │   ├── service.py             # DataService protocol (public contract)
        │   ├── fundamentals_repo.py   # FundamentalsRepository protocol
        │   ├── coverage_repo.py       # CoverageRepository protocol
        │   └── provider.py            # FundamentalsProvider protocol
        ├── adapters/                  # Secondary Adapters (Infrastructure & Persistence)
        │   ├── __init__.py
        │   ├── duckdb/
        │   │   ├── __init__.py
        │   │   ├── store.py           # DuckDbFundamentalsStore (implements FundamentalsRepository)
        │   │   ├── corporate.py       # DuckDbCorporateActionsStore
        │   │   └── schema.py          # DDL migrations and schema versioning
        │   ├── ledger/
        │   │   ├── __init__.py
        │   │   └── json_ledger.py     # JsonCoverageRepository (implements CoverageRepository)
        │   └── providers/
        │       ├── nse_filings.py     # Official NSE/BSE corporate filings adapter
        │       └── yfinance_adapter.py# Experimental adapter (clearly quarantined)
        ├── service/                   # Inbound Service Layer
        │   ├── __init__.py
        │   └── service.py             # DataServiceImpl coordinating repositories & providers
        └── cli/                       # Primary Driving Adapter (CLI entrypoint)
            ├── __init__.py
            ├── main.py                # Typer CLI root
            ├── status.py              # honba-data status inspection command
            └── ingest.py              # Ingestion workflow commands
```

### 2.1 Domain Events
Downstream consumers (such as the strategy screener or signal engine) subscribe to domain events rather than polling DuckDB:
- `FundamentalsIngested`: Emitted when new annual/quarterly statements are persisted. Includes symbol, period type (`FY`/`FQ`), fiscal year, and row count.
- `CoverageLedgerUpdated`: Emitted when coverage intervals are extended or verified.
- `DataQualityAlertRaised`: Emitted when an anomaly (e.g. negative cash balance, missing filing) is detected.

---

## 3. Detailed Phase-by-Phase Execution Roadmap

### Phase 1: Dependency Contract & Shared Kernel Alignment (P0) — COMPLETED
- [x] **1.1 Decouple `honba-data` from implicit `honba.domain`**
  - Removed top-level direct import of `honba.domain.bar` and `honba.domain.instrument`.
  - Defined self-contained Domain Models & Protocols within `honba_data.domain`:
    - `InstrumentId` (Value Object: `symbol`, `exchange`).
    - `Bar` (Value Object: `instrument_id`, `ts_event`, `open`, `high`, `low`, `close`, `volume`).
    - `FinancialStatement` (Entity: `symbol`, `period_type`, `fiscal_date`, `filing_date`, `line_items`).
    - `FinancialRatios` (Entity: `symbol`, `fiscal_date`, `roce`, `roe`, `debt_to_equity`, `free_cash_flow`).
- [x] **1.2 Introduce Hexagonal Domain Events**
  - Created `honba_data.domain.events`:
    - `FundamentalsIngested`
    - `CoverageLedgerUpdated`
    - `DataQualityAlertRaised`
    - `IngestionFailed`
- [x] **1.3 Package & Build Configuration Clean-up**
  - Updated `pyproject.toml` with strict semantic version pins: `duckdb>=1.0.0`, `pyarrow>=14.0.0`, `pandas>=2.0.0`, `rich>=13.0.0`, `typer>=0.12.0`.
  - Added optional extras: `[project.optional-dependencies]` for `dev`, `experimental` (e.g., `yfinance`), and `arrow-flight`.

### Phase 2: Interface-First `DataService` & Tactical DDD Repositories (P0) — COMPLETED
- [x] **2.1 Implement `DataService` Protocol (`ports/service.py`)**
  - Declared canonical interface contract:
    ```python
    class DataService(Protocol):
        def get_fundamentals(self, symbol: str, period: str = "FY") -> Sequence[FinancialStatement]: ...
        def get_ratios(self, symbol: str, as_of: date | None = None) -> Sequence[FinancialRatios]: ...
        def get_bars(
            self,
            instrument: InstrumentId,
            timeframe: str,
            interval: DateInterval,
        ) -> Sequence[Bar]: ...
        def coverage(
            self,
            instrument: InstrumentId,
            timeframe: str,
        ) -> Sequence[CoverageRecord]: ...
    ```
- [x] **2.2 Implement Repositories Ports**
  - `ports/fundamentals_repo.py`: `FundamentalsRepository` protocol (`save_company`, `save_statements`, `save_ratios`, `find_by_symbol`, `query_point_in_time`).
  - `ports/coverage_repo.py`: `CoverageRepository` protocol (`load_records`, `update_interval`, `detect_gaps`).
- [x] **2.3 Refactor DuckDB Engine into Adapter (`adapters/duckdb/store.py`)**
  - Moved raw SQL from store scripts into `DuckDbFundamentalsStore` implementing `FundamentalsRepository`.
  - Ensured schema migrations are isolated in `adapters/duckdb/schema.py`.
- [x] **2.4 Concrete Service Implementation (`service/service.py`)**
  - Implemented `DataServiceImpl` implementing `DataService`, injecting `FundamentalsRepository` and `CoverageRepository`.

### Phase 3: Market Data Ownership Resolution & Lakehouse Convergence (P1) — COMPLETED
- [x] **3.1 Lakehouse Parquet Convergence**
  - Replaced legacy float-based Parquet bar store with Hive-partitioned Lakehouse structure (`data/catalog/1D/`, `data/catalog/5M/`).
  - Added compatibility layer supporting both DuckDB and PyArrow Parquet scanners with decimal precision handling.
- [x] **3.2 JSON Ledger Formalization & Interval Algebra**
  - Refactored `JsonCoverageRepository` to implement `CoverageRepository`.
  - Implemented interval union, intersection, and difference algebra in `domain/coverage.py` (`subtract_intervals`).
  - Added SHA-256 partition-level checksums to ledger records for bit-level immutability verification.

### Phase 4: TDD Rigor, Invariant Enforcement & Property-Based Testing (P1) — COMPLETED
- [x] **4.1 Property-Based Testing with `Hypothesis`**
  - Added `tests/unit/test_coverage_algebra.py` verifying interval algebra properties:
    - *Idempotence*: $A \cup A = A$ and $A \setminus \emptyset = A$.
    - *Disjointness*: Intervals in coverage ledger must never overlap; union produces strictly disjoint sorted segments.
    - *Completeness*: For any interval $I$ and covered subset $C$, $(I \setminus C) \cap C = \emptyset$.
- [x] **4.2 Point-in-Time (PIT) Invariant Verification**
  - Expanded test suite in `tests/unit/test_point_in_time.py`:
    - Given a query `as_of = '2024-05-01'`, statements filed on `'2024-05-15'` (even for Q4 2023) MUST NOT be visible.
    - Verified zero look-ahead bias across all fundamental screener queries with staggered corporate filings.
    - Verified point-in-time enforcement through `DataService.get_ratios(symbol, as_of=...)`.
- [x] **4.3 DataService Contract Verification Suite**
  - Implemented reusable contract test suite `tests/contract/test_data_service.py` (`BaseDataServiceContractTests`).
  - Tested protocol conformance, statement querying, PIT ratio queries, bar interval slicing, and coverage ledger integration across both `InMemoryDataService` and `DuckDbLakehouseDataService`.
- [x] **4.4 Quarantine Experimental Drivers**
  - Marked `test_yfinance_driver.py` with `@pytest.mark.experimental` and `@pytest.mark.integration`.
  - Configured custom markers in `pytest.ini` and `pyproject.toml`.
  - Isolated external network calls behind mock HTTP adapters for 100% deterministic unit test execution.

### Phase 5: Concurrency Isolation & Lakehouse Observability (P2) — COMPLETED
- [x] **5.1 Documented Concurrency Model & Runtime Isolation**
  - Explicitly documented that `honba-data` is a batch/offline lakehouse system, decoupled from `honba-ya`'s pinned-thread actor runtime.
  - Implemented bounded concurrency with backpressure (`BoundedExecutor` and `async_bounded_gather` with `asyncio.Semaphore`) in `src/honba_data/concurrency.py` and download pipelines.
  - Prohibited uncoordinated `ThreadPoolExecutor` execution in live trading paths with `LiveActorConcurrencyViolation` runtime guard.
- [x] **5.2 Structured Ingestion Telemetry**
  - Defined structured ingestion event schema in `src/honba_data/telemetry.py` and `src/honba_data/domain/events.py` (`IngestionCompleted` / `IngestionTelemetry`):
    ```json
    {
      "timestamp": "2026-10-24T13:45:00Z",
      "event": "ingestion_completed",
      "universe": "NIFTY_100",
      "symbols_attempted": 100,
      "symbols_succeeded": 100,
      "symbols_failed": 0,
      "rows_written": 2450,
      "coverage_delta_days": 365,
      "duration_seconds": 18.4
    }
    ```
  - Added telemetry listener subsystem (`register_telemetry_listener`, `emit_ingestion_telemetry`) and wired it into batch pipelines.
- [x] **5.3 Lakehouse Health Status CLI (`honba-data status`)**
  - Implemented `honba-data status` CLI command in `src/honba_data/cli/status.py` rendering rich terminal tables:
    - Lakehouse Storage & Inventory Overview (scrip counts, records, fiscal date range, disk usage).
    - Coverage health per scrip and index (Large-Cap Nifty 100, Mid-Cap Nifty Midcap 150, Market Data OHLCV).
    - Missing quarterly filings and overdue reporting dates under SEBI LODR Regulation 33.
    - Provider error rates and maturity scorecards (Official NSE vs quarantined yfinance).
    - Audited ground-truth benchmark reconciliation (TCS, Reliance, Britannia) with **0.00% discrepancy**.
    - IndAS/GAAP accounting identity invariant audits (0 errors across all 5 mathematical identities).
    - Structured `--json` export for automated observability and monitoring.

---

## 4. Specific Changes Matrix

| Area | Current State | Required Target Change | Phase |
| :--- | :--- | :--- | :--- |
| **Dependency Contract** | Implicit unpinned import of `honba.domain` | Self-contained domain models and protocols (`Bar`, `InstrumentId`); zero upward dependency | **Phase 1 (Done)** |
| **Service Contract** | README promises `DataService`, none implemented | Create `DataService` protocol in `ports/service.py`; implement in `service/service.py` | **Phase 2 (Done)** |
| **Tactical DDD** | Concrete DuckDB SQL script directly in store | `FundamentalsRepository` & `CoverageRepository` protocols; move SQL to `adapters/` | **Phase 2 (Done)** |
| **Domain Events** | No events; polling required | Introduce `FundamentalsIngested`, `CoverageLedgerUpdated`, `DataQualityAlertRaised` | **Phase 1 (Done)** |
| **Parquet Lakehouse** | Incomplete stub with `f64` bars & JSON ledger | Delegate market data to `honba-me` or align schema with Hive `Decimal128` | **Phase 3 (Done)** |
| **TDD & Invariants** | Basic DuckDB schema tests; ad-hoc yfinance test | Property-based tests (`Hypothesis`) for coverage algebra & strict point-in-time queries | **Phase 4 (Done)** |
| **Concurrency Model** | Ad-hoc `ThreadPoolExecutor` & polling thread | Isolate as batch/offline lakehouse; bounded backpressure; zero live actor stalling | **Phase 5 (Done)** |
| **Observability** | Only basic `logging.getLogger` | Structured JSON ingestion telemetry + `honba-data status` CLI command | **Phase 5 (Done)** |
| **Provider Maturity** | `yfinance` mixed in as if production ready | Label `yfinance` as experimental; prioritize official NSE filings & bhavcopy | **Phase 5 (Done)** |

---

## 5. Verification & Acceptance Criteria

1. **Clean Installation**: `pip install -e .` succeeds in a clean Python 3.10+ virtualenv without undeclared dependency breaks.
2. **Interface Conformance**: `DataService` contract test suite passes against both in-memory and DuckDB-backed adapters.
3. **Point-in-Time Invariant**: `test_point_in_time.py` verifies zero look-ahead bias across 100% of tested corporate filings.
4. **Coverage Monotonicity**: Property-based tests pass $10^4$ iterations with zero overlapping intervals or inconsistent ledger states.
5. **Architectural Parity**: `honba-data status` cleanly outputs lakehouse health, filing coverage, and SEBI audited ground-truth reconciliation with **0.00% discrepancy**.
