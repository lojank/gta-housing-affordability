"""Toronto housing affordability pipeline: download -> clean -> load into SQLite -> export CSVs."""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "housing.db"
SQL = ROOT / "sql"
EXPORTS = ROOT / "exports"
REPORTS = ROOT / "reports"

REGION = "Toronto, Ontario"
START = "2010-01"
INCOME_BASE_YEAR = 2024
USER_AGENT = "Mozilla/5.0 (housing-affordability-project)"
MISSING_FLAGS = {"..": "not available", "x": "suppressed", "F": "too unreliable to publish"}

STATCAN = {
    "price_index": {
        "table": "18100205",
        "uom": "Index, 201612=100",
        "filters": {"New housing price indexes": "Total (house and land)"},
    },
    "cpi": {
        "table": "18100004",
        "uom": "2002=100",
        "filters": {"Products and product groups": "All-items"},
    },
    "rent_2br": {
        "table": "34100133",
        "uom": "Dollars",
        "filters": {
            "Type of structure": "Row and apartment structures of three units and over",
            "Type of unit": "Two bedroom units",
        },
    },
    "income_real_2024": {
        "table": "11100190",
        "uom": "2024 constant dollars",
        "filters": {
            "Income concept": "Median total income",
            "Economic family type": "Economic families and persons not in an economic family",
        },
    },
}
VALET = {"policy_rate": "V39079", "mortgage_rate_5y": "V80691335"}


def log(issues: list[dict], source: str, check: str, ref_date: str, detail: str, action: str) -> None:
    issues.append({"source": source, "check_name": check, "ref_date": ref_date, "detail": detail, "action": action})


def payment(principal: float, annual_rate_pct: float, years: int = 25) -> float:
    """Monthly payment on a Canadian fixed-rate mortgage (interest compounded semi-annually)."""
    i = (1 + annual_rate_pct / 200) ** (1 / 6) - 1
    return principal * i / (1 - (1 + i) ** (-12 * years))


# ---------- download ----------

def fetch(url: str) -> bytes:
    # StatCan returns 403 without a browser-like User-Agent.
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read()
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def download(refresh: bool) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for src in STATCAN.values():
        path = RAW / f"{src['table']}.zip"
        if path.exists() and not refresh:
            continue
        meta = json.loads(fetch(f"https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/{src['table']}/en"))
        if meta.get("status") != "SUCCESS":
            raise RuntimeError(f"StatCan table {src['table']}: {meta}")
        path.write_bytes(fetch(meta["object"]))
        print(f"downloaded StatCan {src['table']}")
    for series in VALET.values():
        path = RAW / f"{series}.json"
        if path.exists() and not refresh:
            continue
        # Start a year early so the first month's rate change can be measured.
        path.write_bytes(fetch(f"https://www.bankofcanada.ca/valet/observations/{series}/json?start_date=2009-01-01"))
        print(f"downloaded Bank of Canada {series}")


# ---------- clean and validate ----------

def read_statcan(table: str, filters: dict[str, str]) -> pd.DataFrame:
    columns = {"REF_DATE", "GEO", "UOM", "VALUE", "STATUS", *filters}
    with zipfile.ZipFile(RAW / f"{table}.zip") as archive, archive.open(f"{table}.csv") as f:
        return pd.read_csv(f, usecols=lambda c: c in columns, dtype=str, keep_default_na=False, encoding="utf-8-sig")


def clean_statcan(df: pd.DataFrame, name: str, uom: str, filters: dict[str, str], issues: list[dict]) -> pd.DataFrame:
    """Return Toronto rows indexed by REF_DATE with `value` and `estimated` columns."""
    rows = df[df["GEO"] == REGION]
    for column, member in filters.items():
        rows = rows[rows[column] == member]
    if rows.empty:
        raise ValueError(f"{name}: no rows for {REGION} with {filters}")
    units = set(rows["UOM"])
    if units != {uom}:
        raise ValueError(f"{name}: expected unit {uom!r}, found {sorted(units)}")

    rows = rows[rows["REF_DATE"] >= START[:4]]
    out = pd.DataFrame({
        "ref_date": rows["REF_DATE"].to_numpy(),
        "status": rows["STATUS"].str.strip().to_numpy(),
        "value": pd.to_numeric(rows["VALUE"], errors="coerce").to_numpy(),
    })

    for flag, meaning in MISSING_FLAGS.items():
        hit = out["status"] == flag
        for ref_date in out.loc[hit, "ref_date"]:
            log(issues, name, "flagged value", ref_date, f"StatCan flag '{flag}' ({meaning})", "treated as missing")
        out.loc[hit, "value"] = float("nan")

    estimated = out["status"] == "E"
    for ref_date in out.loc[estimated, "ref_date"]:
        log(issues, name, "estimate flag", ref_date, "StatCan flag 'E' (use with caution)", "kept and marked as estimated")

    blank = out["value"].isna() & ~out["status"].isin(list(MISSING_FLAGS))
    for ref_date in out.loc[blank, "ref_date"]:
        log(issues, name, "empty value", ref_date, "no value and no StatCan flag", "treated as missing")

    duplicate = out["ref_date"].duplicated(keep="last")
    for ref_date in out.loc[duplicate, "ref_date"]:
        log(issues, name, "duplicate", ref_date, "more than one row for this period", "kept the last row")

    impossible = out["value"] <= 0
    for ref_date in out.loc[impossible, "ref_date"]:
        log(issues, name, "impossible value", ref_date, "value is zero or negative", "treated as missing")
    out.loc[impossible, "value"] = float("nan")

    out["estimated"] = estimated
    return out[~duplicate].set_index("ref_date")[["value", "estimated"]]


def read_valet(series: str, name: str, issues: list[dict]) -> pd.Series:
    data = json.loads((RAW / f"{series}.json").read_text(encoding="utf-8"))
    values = {}
    for obs in data["observations"]:
        raw = obs.get(series, {}).get("v")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            log(issues, name, "non-numeric value", obs["d"], f"value {raw!r}", "skipped")
            continue
        if not 0 <= value <= 25:
            log(issues, name, "impossible value", obs["d"], f"rate {value}% outside 0-25%", "skipped")
            continue
        values[obs["d"]] = value
    series_values = pd.Series(values, dtype=float)
    series_values.index = pd.to_datetime(series_values.index)
    return series_values.sort_index()


def to_months(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.to_datetime(df.index).to_period("M")
    return df


def annual_to_monthly(annual: pd.Series, months: pd.PeriodIndex) -> tuple[pd.Series, pd.Series]:
    """Give every month its year's value; carry the last year forward. Returns (values, carried_forward)."""
    annual = annual.dropna()
    last_year = annual.index.max()
    years = months.year
    values = pd.Series(annual.reindex(years).to_numpy(), index=months, dtype=float)
    carried = pd.Series(years > last_year, index=months)
    values[carried] = annual[last_year]
    return values, carried


def rate_periods(rate: pd.Series) -> pd.Series:
    """Label months 'rising'/'falling' from first to last move of each run of same-direction changes, else 'flat'."""
    change = rate.diff()
    moves = change[change.fillna(0) != 0]
    labels = pd.Series("flat", index=rate.index)
    if moves.empty:
        return labels
    up = moves > 0
    run_id = (up != up.shift()).cumsum()
    for _, run in moves.groupby(run_id):
        labels.loc[run.index.min():run.index.max()] = "rising" if run.iloc[0] > 0 else "falling"
    return labels


def build_monthly(issues: list[dict]) -> pd.DataFrame:
    cleaned = {
        name: clean_statcan(read_statcan(src["table"], src["filters"]), name, src["uom"], src["filters"], issues)
        for name, src in STATCAN.items()
    }
    price = to_months(cleaned["price_index"])
    cpi = to_months(cleaned["cpi"])
    end = min(price["value"].last_valid_index(), cpi["value"].last_valid_index())
    months = pd.period_range(START, end, freq="M")

    monthly = pd.DataFrame(index=months)
    estimated: dict[str, pd.Series] = {}
    for name, df in (("price_index", price), ("cpi", cpi)):
        monthly[name] = df["value"].reindex(months)
        estimated[name] = df["estimated"].reindex(months, fill_value=False).astype(bool)

    for name in ("rent_2br", "income_real_2024"):
        annual = cleaned[name]
        annual.index = annual.index.astype(int)
        values, carried = annual_to_monthly(annual["value"], months)
        monthly[name] = values
        flagged = annual["estimated"].reindex(months.year, fill_value=False).to_numpy(dtype=bool)
        estimated[name] = carried | flagged
        if carried.any():
            first = months[carried.to_numpy()][0]
            log(issues, name, "data ends early", str(first),
                f"latest annual value is for {annual['value'].dropna().index.max()}",
                f"carried forward to {end}; marked as estimated")

    cpi_by_year = cpi["value"].groupby(cpi["value"].index.year).mean()
    last_cpi = cpi["value"].last_valid_index()
    if last_cpi.month != 12:
        log(issues, "cpi", "partial year", str(last_cpi.year), f"CPI available to {last_cpi} only",
            f"{last_cpi.year} income uses the average CPI to date")
    monthly["income_nominal"] = monthly["income_real_2024"] * (
        cpi_by_year.reindex(months.year).to_numpy() / cpi_by_year[INCOME_BASE_YEAR]
    )

    policy = read_valet(VALET["policy_rate"], "policy_rate", issues)
    mortgage = read_valet(VALET["mortgage_rate_5y"], "mortgage_rate_5y", issues)
    monthly["policy_rate"] = policy.groupby(policy.index.to_period("M")).mean().reindex(months)
    monthly["mortgage_rate_5y"] = mortgage.groupby(mortgage.index.to_period("M")).mean().reindex(months)
    policy_month_end = policy.groupby(policy.index.to_period("M")).last()
    monthly["rate_period"] = rate_periods(policy_month_end).reindex(months)

    for column in monthly.columns:
        for month in monthly.index[monthly[column].isna()]:
            log(issues, column, "missing month", str(month), "no value for this month", "left blank")

    flags = pd.DataFrame(estimated)
    monthly["estimated_notes"] = flags.apply(lambda row: ", ".join(c for c in flags.columns if row[c]), axis=1)
    monthly["is_estimated"] = (monthly["estimated_notes"] != "").astype(int)
    monthly.insert(0, "year", months.year)
    monthly.insert(0, "month", months.astype(str))
    return monthly.reset_index(drop=True)


# ---------- load and export ----------

def create_db(con: sqlite3.Connection) -> None:
    con.executescript((SQL / "schema.sql").read_text(encoding="utf-8"))
    con.executescript((SQL / "views.sql").read_text(encoding="utf-8"))


def load_and_export(monthly: pd.DataFrame, issues: list[dict]) -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(exist_ok=True)
    REPORTS.mkdir(exist_ok=True)
    quality = pd.DataFrame(issues, columns=["source", "check_name", "ref_date", "detail", "action"])
    with closing(sqlite3.connect(DB)) as con:
        create_db(con)
        monthly.to_sql("monthly", con, if_exists="append", index=False)
        quality.to_sql("data_quality", con, if_exists="append", index=False)
        con.commit()
        affordability = pd.read_sql("SELECT * FROM affordability ORDER BY month", con)
    affordability.to_csv(EXPORTS / "affordability.csv", index=False)
    quality.to_csv(REPORTS / "data_quality.csv", index=False)

    print(f"months loaded: {len(monthly)} ({monthly['month'].iloc[0]} to {monthly['month'].iloc[-1]})")
    print("data quality issues:")
    print(quality.groupby(["source", "check_name"]).size().to_string() if len(quality) else "  none")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="download fresh copies even if raw files exist")
    args = parser.parse_args(argv)
    download(args.refresh)
    issues: list[dict] = []
    monthly = build_monthly(issues)
    load_and_export(monthly, issues)


if __name__ == "__main__":
    main()
