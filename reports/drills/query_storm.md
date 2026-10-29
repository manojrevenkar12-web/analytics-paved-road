# Drill: query_storm (simulated)

**Question.** Can one runaway automation make the platform slow for everyone else?

**Injected.** 40 queries/s from one service user 11:00-11:10 UTC.

| Time (UTC) | What happened |
|---|---|
| 11:00 | An n8n workflow loops: 40 distinct queries a second. |
| 11:00 | Admission control holds it to its 2 concurrent queries; the rest wait up to 5 s and are refused with 429. |
| 11:10 | The workflow is stopped. |

**Results**

```json
{
  "storm_queries": 24000,
  "storm_refused_429": 16268,
  "others_without_storm": {
    "queries": 166,
    "answered": 138,
    "failed": 0,
    "p50_s": 0.035,
    "p95_s": 3.017
  },
  "others_with_admission": {
    "queries": 164,
    "answered": 136,
    "failed": 0,
    "p50_s": 0.035,
    "p95_s": 3.135
  },
  "others_without_admission": {
    "queries": 162,
    "answered": 134,
    "failed": 0,
    "p50_s": 0.035,
    "p95_s": 2477.085
  }
}
```

**Verdict:** pass
