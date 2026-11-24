-- Monthly payment uses Canadian semi-annual compounding, 25-year amortization (300 payments).
CREATE VIEW affordability AS
WITH rates AS (
    SELECT m.*, pow(1 + mortgage_rate_5y / 200.0, 1.0 / 6) - 1 AS i
    FROM monthly m
),
calc AS (
    SELECT r.*,
           100000 * i / (1 - pow(1 + i, -300)) AS payment_per_100k,
           rent_2br * 12 / income_nominal AS rent_to_income
    FROM rates r
),
scored AS (
    SELECT c.*, price_index * payment_per_100k / (income_nominal / 12) AS affordability_raw
    FROM calc c
),
base AS (
    SELECT affordability_raw AS base_raw, cpi AS base_cpi FROM scored WHERE month = '2016-12'
)
SELECT s.month,
       s.month || '-01' AS month_start,
       s.year,
       s.rate_period,
       s.is_estimated,
       s.estimated_notes,
       s.price_index,
       s.price_index / s.cpi * b.base_cpi AS real_price_index,
       s.cpi,
       s.rent_2br,
       s.income_nominal,
       s.policy_rate,
       s.mortgage_rate_5y,
       s.payment_per_100k,
       s.affordability_raw / b.base_raw * 100 AS affordability_index,
       s.rent_to_income,
       100.0 * (s.price_index / lag(s.price_index, 12) OVER w - 1) AS price_yoy_pct,
       100.0 * (s.rent_2br / lag(s.rent_2br, 12) OVER w - 1) AS rent_yoy_pct,
       100.0 * (s.affordability_raw / lag(s.affordability_raw, 12) OVER w - 1) AS affordability_yoy_pct
FROM scored s
LEFT JOIN base b ON 1 = 1
WINDOW w AS (ORDER BY s.month);
