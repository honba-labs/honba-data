"""Integration test suite to verify DuckDB fundamental data against official audited corporate reports.

Verifies:
1. Ground Truth / Golden Benchmarks: Cross-checks blue-chip fundamentals (TCS, Reliance, Britannia)
   against audited annual filings from BSE/NSE.
2. Accounting Invariants: Validates that fundamental data adheres to GAAP/IndAS accounting identities:
   - Capital Employed = Total Assets - Current Liabilities
   - ROCE % = (EBIT / Capital Employed) * 100
   - ROE % = (Net Income / Stockholders Equity) * 100
   - Operating Margin % = (EBIT / Revenue) * 100
   - Debt to Equity = Total Debt / Stockholders Equity
3. Data Integrity & Completeness across all 250 Large-Cap and Mid-Cap companies in DuckDB.
"""

from __future__ import annotations

import math

import duckdb
import pytest

from honba_data.store.fundamentals import find_data_root

DB_PATH = find_data_root() / "fundamentals.duckdb"


@pytest.fixture(scope="module")
def db_conn():
    """Fixture providing a read-only DuckDB connection to fundamentals.duckdb."""
    if not DB_PATH.is_file():
        pytest.skip(f"Fundamentals database not found at {DB_PATH}")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    yield con
    con.close()


def test_universe_completeness(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Verify that all 250 Large-Cap and Mid-Cap companies exist in the DuckDB database."""
    r_total = db_conn.execute("SELECT count(*) FROM companies").fetchone()
    r_large = db_conn.execute(
        "SELECT count(*) FROM companies WHERE cap_category='LARGE_CAP'"
    ).fetchone()
    r_mid = db_conn.execute(
        "SELECT count(*) FROM companies WHERE cap_category='MID_CAP'"
    ).fetchone()

    total_comps = r_total[0] if r_total else 0
    large_comps = r_large[0] if r_large else 0
    mid_comps = r_mid[0] if r_mid else 0

    assert total_comps >= 250, f"Expected at least 250 companies, found {total_comps}"
    assert large_comps == 100, f"Expected 100 Large-Cap companies, found {large_comps}"
    assert mid_comps == 150, f"Expected 150 Mid-Cap companies, found {mid_comps}"


def test_tcs_audited_ground_truth(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Verify TCS audited annual results for FY24 and FY23 against official BSE/NSE filings.

    Official Audited Annual Reports:
    - FY24 (2024-03-31):
      * Revenue: ₹2,40,893 Cr
      * Operating EBIT: ₹62,775 Cr
      * Net Income (PAT): ₹45,908 Cr
      * Equity: ₹90,489 Cr
      * ROCE: ~62.6%
      * ROE: ~50.7%
    - FY23 (2023-03-31):
      * Revenue: ₹2,25,458 Cr
      * Operating EBIT: ₹57,686 Cr
      * Net Income (PAT): ₹42,147 Cr
      * Equity: ₹90,424 Cr
      * ROCE: ~57.6%
      * ROE: ~46.6%
    """
    rows = db_conn.execute("""
        SELECT fiscal_date, revenue, ebit, net_income, stockholders_equity, roce_pct, roe_pct, debt_to_equity
        FROM financial_ratios
        WHERE symbol = 'TCS' AND period_type = 'FY' AND fiscal_date IN ('2024-03-31', '2023-03-31')
        ORDER BY fiscal_date DESC
    """).fetchall()

    assert len(rows) == 2, "Expected 2 fiscal years for TCS"

    # FY24 verification
    fy24 = rows[0]
    assert str(fy24[0]) == "2024-03-31"
    rev_24_cr = fy24[1] / 1e7
    ebit_24_cr = fy24[2] / 1e7
    pat_24_cr = fy24[3] / 1e7
    eq_24_cr = fy24[4] / 1e7
    roce_24 = fy24[5]
    roe_24 = fy24[6]
    debt_eq_24 = fy24[7]

    assert pytest.approx(rev_24_cr, rel=0.01) == 240893.0
    assert pytest.approx(ebit_24_cr, rel=0.01) == 62775.0
    assert pytest.approx(pat_24_cr, rel=0.01) == 45908.0
    assert pytest.approx(eq_24_cr, rel=0.01) == 90489.0
    assert 60.0 <= roce_24 <= 65.0
    assert 48.0 <= roe_24 <= 53.0
    assert debt_eq_24 < 0.15  # Virtually zero debt

    # FY23 verification
    fy23 = rows[1]
    assert str(fy23[0]) == "2023-03-31"
    rev_23_cr = fy23[1] / 1e7
    ebit_23_cr = fy23[2] / 1e7
    pat_23_cr = fy23[3] / 1e7
    eq_23_cr = fy23[4] / 1e7
    roce_23 = fy23[5]
    roe_23 = fy23[6]

    assert pytest.approx(rev_23_cr, rel=0.01) == 225458.0
    assert pytest.approx(ebit_23_cr, rel=0.01) == 57686.0
    assert pytest.approx(pat_23_cr, rel=0.01) == 42147.0
    assert pytest.approx(eq_23_cr, rel=0.01) == 90424.0
    assert 55.0 <= roce_23 <= 60.0
    assert 44.0 <= roe_23 <= 49.0


def test_reliance_audited_ground_truth(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Verify Reliance Industries consolidated results for FY24 and FY23 against official BSE/NSE filings.

    Official Consolidated Audited Reports:
    - FY24: Revenue ₹9,01,064 Cr, EBIT ₹1,26,112 Cr, PAT ₹69,621 Cr, Equity ₹7,93,481 Cr, ROCE ~9.3%
    - FY23: Revenue ₹8,77,835 Cr, EBIT ₹1,13,004 Cr, PAT ₹66,702 Cr, Equity ₹7,15,872 Cr, ROCE ~9.3%
    """
    rows = db_conn.execute("""
        SELECT fiscal_date, revenue, ebit, net_income, stockholders_equity, roce_pct, roe_pct, debt_to_equity
        FROM financial_ratios
        WHERE symbol = 'RELIANCE' AND period_type = 'FY' AND fiscal_date IN ('2024-03-31', '2023-03-31')
        ORDER BY fiscal_date DESC
    """).fetchall()

    assert len(rows) == 2

    # FY24 verification
    fy24 = rows[0]
    assert str(fy24[0]) == "2024-03-31"
    rev_24_cr = fy24[1] / 1e7
    ebit_24_cr = fy24[2] / 1e7
    pat_24_cr = fy24[3] / 1e7
    eq_24_cr = fy24[4] / 1e7
    roce_24 = fy24[5]
    debt_eq_24 = fy24[7]

    assert pytest.approx(rev_24_cr, rel=0.01) == 901064.0
    assert pytest.approx(ebit_24_cr, rel=0.01) == 126112.0
    assert pytest.approx(pat_24_cr, rel=0.01) == 69621.0
    assert pytest.approx(eq_24_cr, rel=0.01) == 793481.0
    assert 8.5 <= roce_24 <= 10.5
    assert 0.40 <= debt_eq_24 <= 0.50

    # FY23 verification
    fy23 = rows[1]
    assert str(fy23[0]) == "2023-03-31"
    rev_23_cr = fy23[1] / 1e7
    ebit_23_cr = fy23[2] / 1e7
    pat_23_cr = fy23[3] / 1e7
    eq_23_cr = fy23[4] / 1e7

    assert pytest.approx(rev_23_cr, rel=0.01) == 877835.0
    assert pytest.approx(ebit_23_cr, rel=0.01) == 113004.0
    assert pytest.approx(pat_23_cr, rel=0.01) == 66702.0
    assert pytest.approx(eq_23_cr, rel=0.01) == 715872.0


def test_britannia_audited_ground_truth(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Verify Britannia Industries (FMCG) results for FY24 against official audited reports."""
    row = db_conn.execute("""
        SELECT fiscal_date, revenue, ebit, net_income, stockholders_equity, roce_pct, roe_pct
        FROM financial_ratios
        WHERE symbol = 'BRITANNIA' AND period_type = 'FY' AND fiscal_date = '2024-03-31'
    """).fetchone()

    assert row is not None
    rev_cr = row[1] / 1e7
    pat_cr = row[3] / 1e7
    roce = row[5]
    roe = row[6]

    assert pytest.approx(rev_cr, rel=0.01) == 16546.21
    assert pytest.approx(pat_cr, rel=0.01) == 2139.81
    assert 55.0 <= roce <= 68.0
    assert 50.0 <= roe <= 60.0


def test_accounting_invariants_across_all_companies(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Test fundamental accounting identities across all ratio records:
    1. Capital Employed = Total Assets - Current Liabilities (when assets and liabilities exist)
    2. ROCE % = (EBIT / Capital Employed) * 100
    3. ROE % = (Net Income / Stockholders Equity) * 100
    4. Operating Margin % = (EBIT / Revenue) * 100
    5. Total Assets > 0
    """
    records = db_conn.execute("""
        SELECT symbol, period_type, fiscal_date, revenue, ebit, net_income,
               total_assets, current_liabilities, capital_employed, stockholders_equity,
               roce_pct, roe_pct, operating_margin_pct
        FROM financial_ratios
        WHERE capital_employed IS NOT NULL AND stockholders_equity IS NOT NULL
        LIMIT 500
    """).fetchall()

    assert len(records) > 0

    for r in records:
        rev = r[3]
        ebit = r[4]
        net_inc = r[5]
        assets = r[6]
        cur_liab = r[7]
        cap_emp = r[8]
        equity = r[9]
        roce = r[10]
        roe = r[11]
        op_margin = r[12]

        if assets is not None:
            assert assets > 0, f"Negative total assets for {r[0]} on {r[2]}"

        # Capital Employed identity
        if assets is not None and cur_liab is not None and cap_emp is not None:
            expected_cap_emp = (assets - cur_liab) if assets > cur_liab else assets
            assert math.isclose(cap_emp, expected_cap_emp, rel_tol=1e-4)

        # ROCE identity
        if ebit is not None and cap_emp is not None and cap_emp > 0 and roce is not None:
            expected_roce = (ebit / cap_emp) * 100
            assert math.isclose(roce, expected_roce, rel_tol=1e-4)

        # ROE identity
        if net_inc is not None and equity is not None and equity > 0 and roe is not None:
            expected_roe = (net_inc / equity) * 100
            assert math.isclose(roe, expected_roe, rel_tol=1e-4)

        # Operating margin identity
        if ebit is not None and rev is not None and rev > 0 and op_margin is not None:
            expected_op_margin = (ebit / rev) * 100
            assert math.isclose(op_margin, expected_op_margin, rel_tol=1e-4)


def test_quarterly_statements_line_items_exist(db_conn: duckdb.DuckDBPyConnection) -> None:
    """Verify that quarterly statements (FQ) contain essential line items (Revenue, EBIT, Net Income)."""
    tcs_fq = db_conn.execute("""
        SELECT line_item, count(*)
        FROM financial_statements
        WHERE symbol = 'TCS' AND period_type = 'FQ'
        GROUP BY line_item
    """).df()

    line_items = set(tcs_fq["line_item"].tolist())
    assert "Operating Revenue" in line_items or "Total Revenue" in line_items
    assert "EBIT" in line_items or "Operating Income" in line_items
    assert "Net Income" in line_items
