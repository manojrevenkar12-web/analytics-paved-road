# n8n-nodes-paved-road

An n8n node for the analytics paved road. Workflows reach the lake only through the query
gateway, as their own service user, so the same access rules, scan limits and audit apply.

| Operation | What it does |
|---|---|
| Run SQL | Spark SQL through the gateway; one item per row. A refusal fails the item with the gateway's message (`denied_column`, `partition_filter_required` …); 429 and 503 are retried with backoff. |
| Ask a Question | A question about the defined metrics; the answer text, the metric query and the rows. |
| Check Freshness | One item per data product that has no data for yesterday, with its owner channel. |

Credentials: the gateway URL, the service user (e.g. `n8n`, role `automation`) and a token.

```bash
npm ci --ignore-scripts && npm test && npm run build
```
