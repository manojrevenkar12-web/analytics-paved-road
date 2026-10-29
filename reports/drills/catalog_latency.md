# Drill: catalog_latency (simulated)

**Question.** Does a slow catalog make every query slow, and does the latency SLO see it?

**Injected.** +2.5 s per catalog load 14:00-15:00 UTC.

| Time (UTC) | What happened |
|---|---|
| 14:00 | Iceberg REST catalog responses slow down by 2.5 s per table load. |
| 15:00 | Catalog latency back to normal. |

**Results**

```json
{
  "fast_within_2s_with_metadata_cache": 0.853448275862069,
  "fast_within_2s_without_cache": 0.0,
  "p95_fast_with_cache_s": 2.555,
  "p95_fast_without_cache_s": 2.555,
  "alerts_with_cache": [],
  "alerts_without_cache": [
    {
      "alert": "page_fast",
      "severity": "page",
      "start": "14:40",
      "end": "15:00"
    },
    {
      "alert": "ticket_slow",
      "severity": "ticket",
      "start": "14:31",
      "end": "20:44"
    }
  ]
}
```

**Verdict:** pass
