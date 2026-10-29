# Drill: bad_batch (real)

**Question.** If a source sends a corrupt batch, does anyone read it before it is caught?

**Injected.** raw.trades for 2026-06-04 with 50 negative amounts and 20 duplicated trade ids.

| Time (UTC) | What happened |
|---|---|
| 04:30 | The batch is written to the audit branch audit-2026-06-04. |
| 04:31 | Contract checks fail: unique on trade_id: 20 duplicates; positive on amount_eur: 50 rows <= 0. |
| 04:31 | main is not moved; the run is quarantined and the owner (#brokerage-data) is told. Readers keep the previous day's data. |
| 05:20 | The source re-sends a clean batch; it passes the checks and is published. |

**Results**

```json
{
  "first_run": "quarantined",
  "failed_checks": [
    "unique on trade_id: 20 duplicates",
    "positive on amount_eur: 50 rows <= 0"
  ],
  "main_unchanged": true,
  "rows_visible_while_quarantined": 0,
  "second_run": "published",
  "rows_visible_after_fix": 3219
}
```

**Verdict:** pass
