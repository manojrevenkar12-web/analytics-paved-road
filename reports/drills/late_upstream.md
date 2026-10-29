# Drill: late_upstream (real)

**Question.** When an upstream batch is late, who is told, and what do consumers see meanwhile?

**Injected.** raw.trades for 2026-06-04 lands 180 minutes after its usual 04:30.

| Time (UTC) | What happened |
|---|---|
| 04:30 | The other raw tables land on time. |
| 06:10 | APR_ProductLate (ticket) for trades to #brokerage-data: no data for 2026-06-04 after 06:00. |
| 07:10 | APR_ProductLate (page) for daily_trading to #brokerage-analytics: no data for 2026-06-04 after 07:00. |
| 07:30 | raw.trades for 2026-06-04 lands, 180 minutes late. |
| 07:30 | trades published for 2026-06-04; the alert resolves. |
| 07:42 | The daily_trading DAG's sensor releases; dbt builds the mart. |
| 07:42 | daily_trading published for 2026-06-04; the alert resolves. |

**Results**

```json
{
  "mart_published": [
    "published",
    "published"
  ],
  "mart_data_through": "2026-06-04",
  "mart_late_minutes": 42,
  "landed": {
    "raw.customers": "2026-06-05T04:30:00",
    "raw.instruments": "2026-06-05T04:30:00",
    "raw.savings_plans": "2026-06-05T04:30:00",
    "raw.prices_daily": "2026-06-05T04:30:00",
    "raw.trades": "2026-06-05T07:30:00",
    "raw.cash_movements": "2026-06-05T04:30:00"
  }
}
```

**Verdict:** pass
