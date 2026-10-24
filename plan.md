# Master Plan: 本場・資 honba-data (Financial Lakehouse & Fundamentals Engine)

> **The analytical fundamentals lakehouse and corporate intelligence subsystem of Honba Labs.**  
> Governs historical corporate financial filings (Annual `FY` and Quarterly `FQ`), balance sheets, cash flow statements, standardized fundamental ratios (ROCE, ROE, Debt/Equity, FCF), corporate actions, and SEBI audited ground-truth cross-verification for Indian equities (Nifty 100 Large-Cap, Nifty Midcap 150, Smallcap 250). Operates as an **Embedded-First, Microservice-Ready** data lakehouse backed by DuckDB and Apache Arrow/Parquet.

---

## 0. Executive Summary & Architectural Audit

An in-depth architectural audit of `honba-data` reveals seven acute and severe structural gaps across dependency management, interface-first design, tactical DDD patterns, testing rigor, concurrency models, observability, and domain boundaries:

| # | Gap & Architectural Weakness | Severity | Subsystem Components Impacted | Core Remedy |
|:---|:---|:---|:---|:---|
| **1** | **Polyrepo Coordination & Dependency Break**: Imports `honba.domain` (`Bar`, `InstrumentId`) in `store/parquet.py` without declaring `honba` or `honba-core` in `pyproject.toml`. Causes hard runtime failure in clean environments. | **P0 (Critical)** | `pyproject.toml`, `store/parquet.py`, CI pipeline | Define self-contained local protocols & domain entities with boundary adapters; preserve unidirectional DAG (`honba-tamashii` -> `honba-data`). |
| **2** | **Interface-First Contract Violation**: README promises a `DataService` port/contract, but no protocol exists. `ParquetBarStore` methods (`append`, `read`) are incomplete stubs that silently return empty lists rather than satisfying a declared interface. | **P0 (Critical)** | `README.md`, `src/honba_data/service/`, `store/parquet.py` | Introduce a formal `DataService` protocol (`get_fundamentals`, `get_bars`, `coverage`) with contract tests, and make stores conform. |
| **3** | **Tactical DDD Patterns Absent**: `DuckDbFundamentalsStore` executes raw SQL and creates schemas directly (Transaction Script). No repository protocols, no aggregate boundaries, and no domain events for downstream consumers (e.g. strategy screener). | **P1 (High)** | `store/fundamentals.py`, `store/coverage.py`, `cli/main.py` | Introduce `FundamentalsRepository` and `CoverageRepository` protocols in `ports/`. Move SQL/JSON storage to `adapters/`. Emit domain events (`FundamentalsIngested`). |
| **4** | **TDD Rigor & Invariant Misalignment**: Tests focus on trivial schema creation rather than core invariants (Parquet read/append round-trips, ledger consistency, point-in-time filing date filtering). Production test suite tests experimental `yfinance` driver. | **P1 (High)** | `tests/unit/`, `tests/integration/` | Implement property-based tests (`Hypothesis`) for coverage ledger algebra and point-in-time correctness (`filing_date <= as_of`). Contract-test `DataService`. |
| **5** | **Ad-Hoc Concurrency & Live-Path Bleed**: `providers/fundamentals.py` uses uncoordinated `ThreadPoolExecutor`; `yfinance_driver.py` uses raw Python threading to poll bars. Incompatible with `honba-ya` pinned-thread Tokio actor runtime. | **P1 (High)** | `providers/fundamentals.py`, `live/yfinance_driver.py` | Formalize `honba-data` as an offline/batch lakehouse. Isolate live ingestion into a dedicated process/channel with bounded backpressure. |
| **6** | **Observability & Lakehouse Health Absent**: No structured events, no OpenTelemetry tracing spans, no metrics on coverage holes, provider success/failure rates, or reconciliation drift. | **P1 (High)** | `cli/main.py`, `providers/fundamentals.py`, `store/coverage.py` | Emit structured ingestion metrics (`symbols_attempted`, `rows_written`, `coverage_delta`). Expose `honba-data status` CLI command for lakehouse health audits. |
| **7** | **Domain Duplication with `honba-me`**: Both `honba-data` and `honba-me` implement Parquet market data storage, with incompatible schemas (`f64` bars in JSON ledger vs `Decimal128` Arrow Hive partitions). | **P1 (High)** | `store/parquet.py`, `store/coverage.py`, `honba-me` boundary | Clarify domain ownership: `honba-me` owns market data (ticks/bars); `honba-data` owns fundamentals and corporate filings. Converge or deprecate redundant Parquet bar storage. |

---

## 1. Architectural Scope & Bounded Contexts

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                honba Platform Topology                                 │
│                                                                                         │
│   ┌─────────────────────────────────────────┐    ┌─────────────────────────────────┐   │
│   │           honba-me (Market Eye)         │    │    honba-data (Lakehouse)       │   │
│   │  - Live WebSocket Feeds (NSE/BSE/NFO)   │    │  - Corporate Financial Filings  │   │
│   │  - Canonical Tick & Bar Lakehouse       │    │  - DuckDB Fundamentals Store    │   │
│   │  - Hive Parquet (Decimal128 Exact)      │    │  - Financial Ratios (ROCE, ROE) │   │
│   │  - NSE Bhavcopy Market Ingestion        │    │  - Corporate Actions & Audits   │   │
│   │  - Real-time Technical Indicators       │    │  - Coverage Ledger & PIT Engine │   │
│   └────────────────────┬────────────────────┘    └────────────────┬────────────────┘   │
│                       │ Arrow Flight / IPC                        │ Service / DuckDB   │
│                       ▼                                           ▼                    │
│   ┌─────────────────────────────────────────────────────────────────────────────────┐   │
│   │                honba-ya / honba-tamashii / honba-strategies                     │   │
│   │  - Algorithmic Trading Engines & Multi-Horizon Strategy Screeners               │   │
│   │  - Fundamental + Technical Confluence Alpha Models                             │   │
│   └─────────────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
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
- [x] **1.1 Self-Contained Domain Contracts & DAG Preservation (`pyproject.toml`)**
  - Resolved dependency boundary: Rather than having Layer 2 `honba-data` depend upward on Layer 4 `honba-tamashii` (which creates an architectural inversion and a cyclic dependency since `honba-tamashii` already imports `honba-data`), `honba-data` is made 100% self-contained.
  - Defined local canonical domain entities, protocols, and boundary adapters in `src/honba_data/domain/` (`InstrumentId`, `Bar`, `InstrumentIdProtocol`, `BarProtocol`, `as_instrument_id`, `as_bar`, `FinancialStatement`, `FinancialRatios`, `Company`).
  - Added first-class domain events in `src/honba_data/domain/events.py` (`FundamentalsIngested`, `CoverageLedgerUpdated`, `DataQualityAlertRaised`, `IngestionFailed`).
  - Structured optional dependency groups in `pyproject.toml`:
    ```toml
    [project.optional-dependencies]
    dev = ["pytest>=8.0.0", "pytest-asyncio", "hypothesis>=6.90.0", "ruff>=0.4.0", "pyright>=1.1.350"]
    experimental = ["yfinance>=0.2.30"]
    arrow-flight = ["pyarrow>=14.0.0"]
    ```
- [x] **1.2 Clean Import Isolation**
  - Excised all dangling `from honba.domain...` imports in `src/honba_data/store/parquet.py`, repointing them to `honba_data.domain.models`.
  - Zero imports of `honba` inside `honba-data`, maintaining a strictly downward dependency graph (`honba-tamashii` -> `honba-data`).
  - Exported `__version__ = "0.1.0"` in `src/honba_data/__init__.py`.
  - Added comprehensive unit tests in `tests/unit/test_domain_models.py` and `tests/unit/test_parquet_store.py`.
  - Verified package installation, zero Pyright diagnostics, clean Ruff formatting/linting, and 100% test pass (57/57 passed).

### Phase 2: Interface-First `DataService` & Tactical DDD Repositories (P0) — COMPLETED
- [x] **2.1 Define Hexagonal Port Protocols (`ports/`)**
  - Defined object-safe typing protocols in `src/honba_data/ports/`:
    - `DataService`:
      ```python
      class DataService(Protocol):
          def get_fundamentals(self, symbol: str, period: str = "FY") -> Sequence[FinancialStatement]: ...
          def get_ratios(
              self, symbol: str, as_of: dt.date | None = None
          ) -> Sequence[FinancialRatios]: ...
          def get_bars(
              self,
              instrument: InstrumentIdProtocol | InstrumentId | Any,
              timeframe: str,
              interval: DateInterval,
          ) -> Sequence[BarProtocol | Bar]: ...
          def coverage(
              self, instrument: InstrumentIdProtocol | InstrumentId | Any, timeframe: str
          ) -> Sequence[CoverageRecord]: ...
      ```
    - `FundamentalsRepository`: `save_company()`, `save_statements()`, `save_ratios()`, `find_by_symbol()`, `find_ratios_by_symbol()`, `query_point_in_time()`, `query_point_in_time_statements()`.
    - `CoverageRepository`: `load_records()`, `update_interval()`, `detect_gaps()`, `append_record()`.
    - `FundamentalsProvider`: `fetch_company()`, `fetch_statements()`, `fetch_ratios()`.
- [x] **2.2 Refactor DuckDB Storage into Ports/Adapters**
  - Migrated schema creation, DDL migrations, and table versioning into `adapters/duckdb/schema.py`.
  - Implemented `FundamentalsRepository` protocol in `adapters/duckdb/store.py` (`DuckDbFundamentalsStore`), supporting both file-backed and `:memory:` connections for hermetic unit testing.
  - Added domain event publishing on statement/ratio ingestion (`FundamentalsIngested`).
  - Re-exported backward-compatible facades in `store/fundamentals.py` and `store/corporate_actions.py`.
- [x] **2.3 Implement Canonical `DataServiceImpl` (`service/service.py`)**
  - Implemented `DataServiceImpl` coordinating `FundamentalsRepository`, `CoverageRepository`, and market data delegates.
  - Added strict delegation semantics: for `get_bars`, delegates to the configured market data store or raises an actionable `NotImplementedError` when unconfigured.
  - Added comprehensive test suites in `tests/unit/test_data_service.py` and `tests/unit/test_duckdb_repo.py`.

### Phase 3: Market Data Ownership Resolution & Lakehouse Convergence (P1) — COMPLETED
- [x] **3.1 Parquet Lakehouse Alignment with `honba-me`**
  - Upgraded `ParquetBarStore` to support canonical Hive partition hierarchy:
    `catalog/timeframe=1D/exchange=NSE/symbol=RELIANCE/year=2025.parquet`.
  - Transparent fallback reading from legacy directory layout (`catalog/1D/NSE/RELIANCE/2025.parquet`).
  - Strict UTC integer nanosecond timestamp verification and OHLCV price/volume invariant validation (`validate_bar`).
  - Added test suite in `tests/unit/test_parquet_hive.py`.
- [x] **3.2 Coverage Ledger Monotonicity**
  - Implemented `JsonCoverageRepository` in `adapters/ledger/json_ledger.py` adhering to `CoverageRepository`.
  - Implemented transactional `.tmp -> replace` atomic writes with deterministic SHA-256 interval checksums (`compute_interval_checksum`).
  - Integrated canonical half-open date interval algebra (`union`, `difference`, `gap_fill`) ensuring non-overlapping canonical intervals.
  - Integrated domain event publication (`CoverageLedgerUpdated`).
  - Added unit test suite in `tests/unit/test_json_ledger.py`.

### Phase 4: TDD Rigor, Invariant Enforcement & Property-Based Testing (P1)
- [ ] **4.1 Property-Based Interval Algebra Tests (`Hypothesis`)**
  - Add property-based tests in `tests/unit/test_coverage_algebra.py`:
    - Invariant 1: Appending a continuous sequence of bars never creates a coverage gap.
    - Invariant 2: The union of contiguous coverage intervals is monotonic and non-overlapping.
    - Invariant 3: Querying a covered interval returns the exact bars appended, with zero duplication.
- [ ] **4.2 Point-in-Time (PIT) Invariant Verification**
  - Implement rigorous test suite verifying point-in-time consistency:
    - Given a query `as_of = '2024-05-01'`, statements filed on `'2024-05-15'` (even for Q4 2023) MUST NOT be visible.
    - Assert zero look-ahead bias across all fundamental screener queries.
- [ ] **4.3 DataService Contract Verification Suite**
  - Implement reusable contract test suite `tests/contract/test_data_service.py` that can be run against any repository/service implementation.
- [ ] **4.4 Quarantine Experimental Drivers**
  - Mark `test_yfinance_driver.py` with `@pytest.mark.experimental` and `@pytest.mark.integration`.
  - Isolate external network calls behind mock HTTP adapters for deterministic unit test execution.

### Phase 5: Concurrency Isolation & Lakehouse Observability (P2)
- [ ] **5.1 Documented Concurrency Model & Runtime Isolation**
  - Explicitly document that `honba-data` is a batch/offline lakehouse system, decoupled from `honba-ya`'s pinned-thread actor runtime.
  - Implement bounded concurrency with backpressure (`asyncio.Semaphore` or bounded worker queue) in download pipelines.
  - Prohibit uncoordinated `ThreadPoolExecutor` execution in live trading paths.
- [ ] **5.2 Structured Ingestion Telemetry**
  - Define structured ingestion event schema:
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
- [ ] **5.3 Lakehouse Health Status CLI (`honba-data status`)**
  - Implement `honba-data status` CLI command rendering rich terminal tables:
    - Coverage health per scrip and index (Large-Cap, Mid-Cap).
    - Missing quarterly filings and overdue reporting dates.
    - Provider error rates and reconciliation scorecards.

---

## 4. Specific Changes Matrix

| Area | Current State | Required Target Change | Phase |
| :--- | :--- | :--- | :--- |
| **Dependency Contract** | Implicit unpinned import of `honba.domain` | Self-contained domain models and protocols (`Bar`, `InstrumentId`); zero upward dependency | **Phase 1 (Done)** |
| **Service Contract** | README promises `DataService`, none implemented | Create `DataService` protocol in `ports/service.py`; implement in `service/service.py` | **Phase 2 (Done)** |
| **Tactical DDD** | Concrete DuckDB SQL script directly in store | `FundamentalsRepository` & `CoverageRepository` protocols; move SQL to `adapters/` | **Phase 2 (Done)** |
| **Domain Events** | No events; polling required | Introduce `FundamentalsIngested`, `CoverageLedgerUpdated`, `DataQualityAlertRaised` | **Phase 1 (Done)** |
| **Parquet Lakehouse** | Incomplete stub with `f64` bars & JSON ledger | Delegate market data to `honba-me` or align schema with Hive `Decimal128` | **Phase 3 (Done)** |
| **TDD & Invariants** | Basic DuckDB schema tests; ad-hoc yfinance test | Property-based tests (`Hypothesis`) for coverage algebra & strict point-in-time queries | **Phase 4** |
| **Concurrency Model** | Ad-hoc `ThreadPoolExecutor` & polling thread | Isolate as batch/offline lakehouse; bounded backpressure; zero live actor stalling | **Phase 5** |
| **Observability** | Only basic `logging.getLogger` | Structured JSON ingestion telemetry + `honba-data status` CLI command | **Phase 5** |
| **Provider Maturity** | `yfinance` mixed in as if production ready | Label `yfinance` as experimental; prioritize official NSE filings & bhavcopy | **Phase 5** |

---

## 5. Verification & Acceptance Criteria

1. **Clean Installation**: `pip install -e .` succeeds in a clean Python 3.10+ virtualenv without undeclared dependency breaks.
2. **Interface Conformance**: `DataService` contract test suite passes against both in-memory and DuckDB-backed adapters.
3. **Point-in-Time Invariant**: `test_point_in_time.py` verifies zero look-ahead bias across 100% of tested corporate filings.
4. **Coverage Monotonicity**: Property-based tests pass $10^4$ iterations with zero overlapping intervals or inconsistent ledger states.
5. **Architectural Parity**: `honba-data status` cleanly outputs lakehouse health, filing coverage, and SEBI audited ground-truth reconciliation with **0.00% discrepancy**.
