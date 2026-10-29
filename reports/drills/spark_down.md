# Drill: spark_down (simulated)

**Question.** If the Spark lane fails in working hours, who notices, how fast, and what still works?

**Injected.** Kyuubi unreachable 10:00-10:40 UTC on 2026-09-23.

| Time (UTC) | What happened |
|---|---|
| 10:00 | Kyuubi stops accepting sessions (the Spark lane is down). |
| 10:00 | The gateway marks the lane down and runs queries up to 1 GiB on DuckDB instead; larger ones get 503 with a retry hint. |
| 10:01 | Spark lane availability: ticket_slow (ticket) fires. |
| 10:03 | Spark lane availability: ticket_fast (ticket) fires. |
| 10:07 | Spark lane availability: page_fast (page) fires. |
| 10:07 | Spark lane availability: page_slow (page) fires. |
| 10:40 | Kyuubi is back; the lane is used again after its health check. |
| 10:42 | Spark lane availability: page_fast resolves. |
| 11:06 | Spark lane availability: page_slow resolves. |
| 12:34 | Spark lane availability: ticket_fast resolves. |
| 16:34 | Spark lane availability: ticket_slow resolves. |

**Results**

```json
{
  "during_outage_people": {
    "queries": 426,
    "answered": 307,
    "failed": 44,
    "p50_s": 0.035,
    "p95_s": 0.321
  },
  "degraded_to_fast_lane": 14,
  "failed_with_degrade": 44,
  "failed_without_degrade": 60,
  "failed_baseline": 463,
  "alerts": [
    {
      "alert": "page_fast",
      "severity": "page",
      "start": "10:07",
      "end": "10:42"
    },
    {
      "alert": "page_slow",
      "severity": "page",
      "start": "10:07",
      "end": "11:06"
    },
    {
      "alert": "ticket_fast",
      "severity": "ticket",
      "start": "10:03",
      "end": "12:34"
    },
    {
      "alert": "ticket_slow",
      "severity": "ticket",
      "start": "10:01",
      "end": "16:34"
    }
  ],
  "alerts_without_degrade": [
    {
      "alert": "page_fast",
      "severity": "page",
      "start": "10:05",
      "end": "10:42"
    },
    {
      "alert": "page_slow",
      "severity": "page",
      "start": "10:05",
      "end": "11:06"
    },
    {
      "alert": "ticket_fast",
      "severity": "ticket",
      "start": "10:03",
      "end": "12:34"
    },
    {
      "alert": "ticket_slow",
      "severity": "ticket",
      "start": "10:01",
      "end": "16:34"
    }
  ],
  "overall_availability_alerts": [
    {
      "alert": "ticket_fast",
      "severity": "ticket",
      "start": "10:32",
      "end": "11:13"
    },
    {
      "alert": "ticket_slow",
      "severity": "ticket",
      "start": "10:07",
      "end": "16:20"
    }
  ]
}
```

**Verdict:** pass
