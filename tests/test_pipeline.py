import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pipeline  # noqa: E402


def test_payment_matches_reference_value():
    assert pipeline.payment(400_000, 5.0) == pytest.approx(2326.42, abs=0.01)


def test_sql_payment_matches_python():
    con = sqlite3.connect(":memory:")
    pipeline.create_db(con)
    con.execute(
        "INSERT INTO monthly (month, year, price_index, cpi, income_nominal, mortgage_rate_5y) "
        "VALUES ('2016-12', 2016, 100, 130, 120000, 5.0)"
    )
    payment, index = con.execute("SELECT payment_per_100k, affordability_index FROM affordability").fetchone()
    assert payment * 4 == pytest.approx(pipeline.payment(400_000, 5.0), abs=0.01)
    assert index == pytest.approx(100)


def test_flagged_values_become_missing_and_are_logged():
    df = pd.DataFrame({
        "REF_DATE": ["2020-01", "2020-02", "2020-03", "2020-04", "2020-05"],
        "GEO": "Toronto, Ontario",
        "UOM": "Dollars",
        "Kind": "Total",
        "VALUE": ["", "", "", "105", "110"],
        "STATUS": ["..", "x", "F", "E", ""],
    })
    issues = []
    out = pipeline.clean_statcan(df, "test", "Dollars", {"Kind": "Total"}, issues)
    assert out["value"].isna().tolist() == [True, True, True, False, False]
    assert out["estimated"].tolist() == [False, False, False, True, False]
    assert sorted(i["check_name"] for i in issues) == ["estimate flag", "flagged value", "flagged value", "flagged value"]


def test_unexpected_unit_stops_pipeline():
    df = pd.DataFrame({"REF_DATE": ["2020"], "GEO": "Toronto, Ontario", "UOM": "Index", "VALUE": ["1"], "STATUS": [""]})
    with pytest.raises(ValueError, match="expected unit"):
        pipeline.clean_statcan(df, "test", "Dollars", {}, [])


def test_annual_values_fill_months_and_carry_forward_is_marked():
    months = pd.period_range("2023-01", "2024-06", freq="M")
    values, carried = pipeline.annual_to_monthly(pd.Series({2023: 1000.0}), months)
    assert (values == 1000.0).all()
    assert not carried.iloc[:12].any()
    assert carried.iloc[12:].all()


def test_rate_periods():
    months = pd.period_range("2022-01", "2022-08", freq="M")
    rates = pd.Series([0.25, 0.25, 0.5, 0.5, 1.0, 1.0, 1.0, 0.75], index=months)
    assert pipeline.rate_periods(rates).tolist() == [
        "flat", "flat", "rising", "rising", "rising", "flat", "flat", "falling",
    ]
