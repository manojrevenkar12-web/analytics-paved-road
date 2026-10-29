# 4. In conversational analytics, the model parses and code computes

Status: accepted

## Context

A language model that writes SQL or sentences with numbers can be confidently wrong, and an
answer to a business question is used as a fact.

## Decision

A question becomes a metric query over `config/metrics.yaml` (by rules, or by Claude with a tool
schema and no access to data). It is validated against the catalog, compiled to SQL and run
through the gateway as the asking user. The answer sentence is written by code from the result
rows. Questions outside the catalog are refused with the reason.

## Consequences

- Answers inherit the gateway's access rules and audit.
- The blind held-out set measures the parser, and a wrong answer counts worse than a refusal.
  The rule-based parser answered 13 of 40 held-out questions wrongly, so answers show the metric
  query they ran, and an interface should ask for confirmation before using an answer.
