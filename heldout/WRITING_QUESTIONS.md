# Writing held-out questions for the conversational analytics

You are writing a blind test of the question parser: the thing that turns a plain-English
question into a metric query. Write it from **`docs/METRICS.md` only** (and this guide). Do not
read anything under `src/`, `tests/`, `config/` (except `config/products/`, which you may read),
or any earlier question sets. Write what real people at a European neobroker would type:
product managers, analysts, marketing, finance. Vary the wording; do not copy the examples in
`docs/METRICS.md` word for word.

The data runs from 2026-06-01 through **2026-09-28** (a Monday). Relative periods ("last month",
"this week", "yesterday") are relative to 2026-09-28, following the rules in `docs/METRICS.md`.

## File

`heldout/ask/questions.yaml`:

```yaml
data_through: 2026-09-28
questions:
  - id: q01
    question: "How much did customers trade in August?"
    answerable: true
    expect:
      metrics: [trading_volume_eur]        # one or more metric names from docs/METRICS.md
      dimensions: []                       # breakdowns, e.g. [country]
      grain: null                          # day | week | month | quarter | null (no time breakdown)
      start: 2026-08-01                    # inclusive
      end: 2026-08-31                      # inclusive; never after 2026-09-28
      filters: {}                          # e.g. {country: [DE], asset_class: [crypto]}
      order: natural                       # natural | metric_desc | metric_asc (for "top", "most", "lowest")
      limit: null                          # e.g. 5 for "top 5", 1 for "which country had the most"
  - id: q31
    question: "Which customer deposited the most last month?"
    answerable: false
    why: "about an individual customer"
```

## What to write

- **30 answerable questions**, spread over all metrics and dimensions: plain totals, breakdowns
  by one dimension, a few by two, time breakdowns (daily/weekly/monthly), filters on one value,
  comparisons of two or three values of a dimension ("Germany vs France" means a breakdown by
  country filtered to those two), "top N" and "which ... had the most/least" (order and limit),
  and a range of periods (yesterday, last week, last 7/14/30 days, this month, last month, a
  named month, a quarter, since a month, between two dates).
- **10 questions the catalog cannot answer**, each with `why`: individual customers, forecasts,
  "why" questions, metrics or dimensions that do not exist, breakdowns a metric does not support
  (check the "Can be broken down by" column), and monthly metrics asked for by day or week or
  for part of a month.
- Country names in questions should be names ("Germany", "the Netherlands") or adjectives
  ("German customers"); the expected filter uses the ISO code.
- Where a question is genuinely ambiguous, make it unambiguous instead: the test is about
  reading questions correctly, not guessing what the writer meant.

The set is run once, and that first run is kept. Nobody tunes the parser against it.
