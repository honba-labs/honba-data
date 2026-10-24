# `honba-data` Agent Guidelines & Architecture Invariants

**Subsystem**: Data (Parquet & DuckDB Lakehouse)
**Language**: Python + DuckDB
**Primary Responsibility**: Corporate actions, splits, dividends, coverage ledgers, and point-in-time historical data store.

---

## 1. Role & Architectural Hints
* `honba-data` manages historical and fundamental data:
  1. **Point-in-Time Data Store**: DuckDB point-in-time as-of joins to eliminate lookahead bias in backtests.
  2. **Corporate Action Adjustment**: Adjusts price and volume series for splits, bonuses, rights issues, and dividend distributions.
  3. **Coverage Ledgers**: Tracks data availability and completeness across scrips and timeframes.
  4. **Parquet Catalog**: Fast zero-copy columnar storage compatible with Apache Arrow.
* **Idea Inheritance**:
  - **QuantConnect (Lean)**: Point-in-time factor files and corporate action handling.

---

## 2. Coding Guidelines (DDD & TDD)
* **Zero Floats Policy**: Decimals for adjusted dividend payouts, ratio splits, and strike steps.
* **TDD & Parallel Test Directory Rule (MANDATORY)**:
  - Tests reside in `tests/` parallel to `src/`.

---

## 3. Development & CI Workflow
* **Linting & Formatting**:
  ```bash
  ruff check .
  ruff format --check .
  ```
* **Testing**:
  ```bash
  pytest -q
  ```
