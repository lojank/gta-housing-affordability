# Dashboard

Open `TorontoHousing.pbip` in Power BI Desktop. It loads `exports/affordability.csv`, so run
`python pipeline.py` first.

## Pages

1. **Trends:** new-home price index (nominal and inflation-adjusted), 2-bedroom rent, and the Bank of
   Canada policy rate coloured by rate period.
2. **Affordability:** latest affordability index, payment per $100k, mortgage rate and rent share, plus
   their trends.
3. **What-if calculator:** sliders for home price, interest rate, down payment, amortization and household
   income; shows the monthly payment, mortgage amount and share of income.

## After opening

- If the data doesn't load, select **Refresh**.
- On another computer, update the folder: **Transform data → Edit parameters → ProjectFolder** (the project
  folder path).

## Model

| Table | Contents |
|---|---|
| `affordability` | One row per month from the CSV, plus the "Latest" and "What-if" measures |
| `Home Price`, `Interest Rate`, `Down Payment`, `Amortization`, `Household Income` | What-if parameters (`GENERATESERIES`) |

The monthly payment measure uses the same formula as `sql/views.sql` (semi-annual compounding).
