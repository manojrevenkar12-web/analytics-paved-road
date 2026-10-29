"""A rule-based question parser: plain English to a MetricQuery over the semantic layer.

It is the default (no API key, reproducible) and the baseline the Claude parser is measured
against. It only maps what it recognises; anything else is refused with the reason, never
guessed: a wrong number stated confidently is worse than no answer.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re

from apr.semantic.layer import MetricQuery, Unanswerable, catalog

COUNTRIES = {
    "DE": ["germany", "german"],
    "FR": ["france", "french"],
    "IT": ["italy", "italian"],
    "ES": ["spain", "spanish"],
    "AT": ["austria", "austrian"],
    "NL": ["netherlands", "dutch", "holland"],
    "SE": ["sweden", "swedish"],
    "BE": ["belgium", "belgian"],
    "PT": ["portugal", "portuguese"],
    "IE": ["ireland", "irish"],
    "FI": ["finland", "finnish"],
    "GR": ["greece", "greek"],
    "SK": ["slovakia", "slovak"],
    "SI": ["slovenia", "slovenian"],
    "LT": ["lithuania", "lithuanian"],
    "LV": ["latvia", "latvian"],
    "EE": ["estonia", "estonian"],
    "LU": ["luxembourg"],
}
ASSET = {
    "stock": ["stocks", "stock", "shares", "equities", "equity"],
    "etf": ["etfs", "etf"],
    "crypto": ["crypto", "cryptocurrencies", "cryptocurrency", "cryptos"],
    "bond": ["bonds", "bond"],
}
CHANNEL = {
    "app": ["mobile app", "the app", "app"],
    "web": ["web", "browser", "desktop"],
    "savings_plan": ["savings plans", "savings plan", "savings-plan"],
}
SEGMENT = {
    "saver": ["savers", "saver segment"],
    "trader": [
        "traders segment",
        "trader segment",
        "active traders segment",
        "by traders",
        "among traders",
        "from traders",
    ],
    "mixed": ["mixed segment", "mixed customers"],
}
ACQ = {
    "organic": ["organic"],
    "referral": ["referral", "referrals", "referred"],
    "paid_social": ["paid social", "social ads"],
    "partner": ["partner", "partners"],
}
VALUE_WORDS = {
    "country": COUNTRIES,
    "asset_class": ASSET,
    "channel": CHANNEL,
    "segment": SEGMENT,
    "acquisition_channel": ACQ,
}
OUT_OF_SCOPE = [
    (
        r"\b(e-?mail|iban|phone|address|full name|names? of|who (is|are|was)|which customers?|customer c\d+|"
        r"birth ?date|date of birth)\b",
        "I answer with aggregate metrics only; questions about individual customers go to the governed SQL "
        "lane, where access rules apply.",
    ),
    (
        r"\b(predict|forecast|will be|next (week|month|quarter|year)|projection)\b",
        "I report what happened; I don't forecast.",
    ),
    (r"\bwhy\b", "I can show what changed, not why; break the metric down by a dimension to look for the cause."),
]
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS |= {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
GRAIN_WORDS = [
    ("day", r"\b(daily|per day|by day|each day|day by day|every day)\b"),
    ("week", r"\b(weekly|per week|by week|each week|week by week|every week)\b"),
    ("month", r"\b(monthly|per month|by month|each month|month by month|every month|month over month)\b"),
    ("quarter", r"\b(quarterly|per quarter|by quarter|each quarter)\b"),
]
DIM_BY = r"\b(by|per|for each|across|split by|broken down by|in each|for every)\s+(the\s+)?"
NUM = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "fourteen": 14,
    "thirty": 30,
    "ninety": 90,
}


def _find(text: str, phrase: str) -> re.Match | None:
    return re.search(r"(?<![\w-])" + re.escape(phrase) + r"(?![\w-])", text)


def _consume(text: str, m: re.Match) -> str:
    return text[: m.start()] + " " * (m.end() - m.start()) + text[m.end() :]


def _quarter(year: int, q: int) -> tuple[dt.date, dt.date]:
    start = dt.date(year, 3 * q - 2, 1)
    end = dt.date(year, 3 * q, calendar.monthrange(year, 3 * q)[1])
    return start, end


def _month(year: int, m: int) -> tuple[dt.date, dt.date]:
    return dt.date(year, m, 1), dt.date(year, m, calendar.monthrange(year, m)[1])


def parse_time(text: str, ref: dt.date) -> tuple[tuple[dt.date, dt.date] | None, str]:
    """A window (inclusive) relative to `ref`, the last complete day of data."""
    t = text
    iso = r"(\d{4}-\d{2}-\d{2})"
    m = re.search(rf"\b(from|between)\s+{iso}\s+(to|and|until)\s+{iso}", t)
    if m:
        return (dt.date.fromisoformat(m.group(2)), dt.date.fromisoformat(m.group(4))), _consume(t, m)
    m = re.search(rf"\b(on|for)\s+{iso}", t) or re.search(rf"\b{iso}\b", t)
    if m:
        d = dt.date.fromisoformat(m.groups()[-1])
        return (d, d), _consume(t, m)
    m = re.search(
        r"\b(?:in the |over the |during the |for the )?(last|past|previous)\s+(\d+|\w+)\s+(days?|weeks?|months?)\b", t
    )
    if m and (m.group(2).isdigit() or m.group(2) in NUM):
        n = int(m.group(2)) if m.group(2).isdigit() else NUM[m.group(2)]
        unit = m.group(3).rstrip("s")
        if unit == "day":
            return (ref - dt.timedelta(days=n - 1), ref), _consume(t, m)
        if unit == "week":
            return (ref - dt.timedelta(days=7 * n - 1), ref), _consume(t, m)
        # last n months: whole months before the current (incomplete) one
        y, mo = ref.year, ref.month
        if ref != _month(y, mo)[1]:
            mo -= 1
        end_y, end_m = (y, mo) if mo >= 1 else (y - 1, 12)
        start_idx = end_y * 12 + end_m - 1 - (n - 1)
        return (dt.date(start_idx // 12, start_idx % 12 + 1, 1), _month(end_y, end_m)[1]), _consume(t, m)
    rules: list[tuple[str, tuple[dt.date, dt.date]]] = []
    week_start = ref - dt.timedelta(days=ref.weekday())
    last_week_end = week_start - dt.timedelta(days=1) if ref.weekday() != 6 else ref
    prev_m = ref.replace(day=1) - dt.timedelta(days=1)
    if ref == _month(ref.year, ref.month)[1]:
        prev_m = ref
    q_now = (ref.month - 1) // 3 + 1
    prev_q = (ref.year, q_now - 1) if q_now > 1 else (ref.year - 1, 4)
    rules += [
        (r"\byesterday\b", (ref, ref)),
        (r"\btoday\b", (ref, ref)),
        (r"\b(this|the current) week\b", (week_start, ref)),
        (r"\b(last|previous|past) week\b", (last_week_end - dt.timedelta(days=6), last_week_end)),
        (r"\b(this|the current) month\b|\bmonth to date\b|\bmtd\b", (ref.replace(day=1), ref)),
        (r"\b(last|previous|past) month\b", _month(prev_m.year, prev_m.month)),
        (r"\b(this|the current) quarter\b|\bquarter to date\b|\bqtd\b", (_quarter(ref.year, q_now)[0], ref)),
        (r"\b(last|previous|past) quarter\b", _quarter(*prev_q)),
        (r"\b(this year|year to date|ytd|so far this year)\b", (dt.date(ref.year, 1, 1), ref)),
    ]
    for pat, window in rules:
        m = re.search(pat, t)
        if m:
            return window, _consume(t, m)
    m = re.search(r"\bq([1-4])(?:\s+(\d{4}))?\b", t)
    if m:
        return _quarter(int(m.group(2) or ref.year), int(m.group(1))), _consume(t, m)
    mon = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(
        rf"\b(?:from|between)\s+({mon})(?:\s+(\d{{4}}))?\s+(?:to|and|until|through)\s+({mon})(?:\s+(\d{{4}}))?\b", t
    )
    if m:
        y1 = int(m.group(2) or m.group(4) or ref.year)
        y2 = int(m.group(4) or y1)
        return (_month(y1, MONTHS[m.group(1)])[0], min(_month(y2, MONTHS[m.group(3)])[1], ref)), _consume(t, m)
    m = re.search(rf"\bsince\s+({mon})(?:\s+(\d{{4}}))?\b", t)
    if m:
        return (_month(int(m.group(2) or ref.year), MONTHS[m.group(1)])[0], ref), _consume(t, m)
    m = re.search(rf"\b(?:in|for|during|of)\s+({mon})(?:\s+(\d{{4}}))?\b", t) or re.search(
        rf"\b({mon})\s+(\d{{4}})\b", t
    )
    if m:
        y = int(m.group(2) or ref.year)
        return _month(y, MONTHS[m.group(1)]), _consume(t, m)
    return None, t


def parse(question: str, ref: dt.date) -> MetricQuery:
    cat = catalog()
    raw = question.strip()
    text = " " + re.sub(r"[?!.,;:]+(?=\s|$)", " ", raw).lower() + " "
    for pat, reason in OUT_OF_SCOPE:
        if re.search(pat, text):
            raise Unanswerable(reason)

    window, text = parse_time(text, ref)

    # metrics: longest synonym first, consumed so its words are not read again
    found: list[tuple[int, str]] = []
    syns = sorted(((s, name) for name, m in cat["metrics"].items() for s in m["synonyms"]), key=lambda x: -len(x[0]))
    for s, name in syns:
        m = _find(text, s)
        if m:
            if name not in [n for _, n in found]:
                found.append((m.start(), name))
            text = _consume(text, m)
    if not found:
        names = ", ".join(m["label"].lower() for m in cat["metrics"].values())
        raise Unanswerable(f"I couldn't match that to a defined metric. I can answer about: {names}.")
    metrics = [n for _, n in sorted(found)]

    grain = None
    for g, pat in GRAIN_WORDS:
        m = re.search(pat, text)
        if m:
            grain = g
            text = _consume(text, m)
            break

    order, limit = "natural", None
    m = re.search(r"\b(top|bottom)\s+(\d+|\w+)\b", text)
    if m and (m.group(2).isdigit() or m.group(2) in NUM):
        order = "metric_desc" if m.group(1) == "top" else "metric_asc"
        limit = int(m.group(2)) if m.group(2).isdigit() else NUM[m.group(2)]
    ranked = re.search(r"\b(most|highest|largest|biggest|best|top|leading|max(imum)?)\b", text)
    ranked_low = re.search(r"\b(least|lowest|smallest|fewest|worst|min(imum)?)\b", text)
    which = re.search(r"\b(which|what)\s+\w+(\s\w+)?\s+(had|has|have|saw|was|were|did|got|made|brought)\b", text)

    # explicit breakdowns: "by country", "per asset class", "which country", "top 5 countries"
    dims: list[str] = []
    for d, spec in sorted(cat["dimensions"].items(), key=lambda kv: -max(len(s) for s in kv[1]["synonyms"])):
        for s in sorted(spec["synonyms"], key=len, reverse=True):
            m = (
                re.search(DIM_BY + re.escape(s) + r"\b", text)
                or re.search(r"\b(which|what|top\s+\d+|top|each|every|bottom\s+\d+)\s+" + re.escape(s) + r"\b", text)
                or re.search(r"\b" + re.escape(s) + r"\s+(breakdown|split)\b", text)
            )
            if m:
                if d not in dims:
                    dims.append(d)
                text = _consume(text, m)
                break
    m = re.search(r"\bby\s+(\w+(?:\s\w+)?)", text)
    if m and m.group(1).split()[0] not in ("the", "a", "an", "how", "what", "much", "far"):
        word = m.group(1).split()[0]
        if word not in ("traders", "savers") and not any(
            word in s for v in VALUE_WORDS.values() for ss in v.values() for s in ss
        ):
            raise Unanswerable(
                f"'{m.group(1)}' is not a dimension I know. Dimensions: "
                f"{', '.join(x['label'].lower() for x in cat['dimensions'].values())}."
            )

    # values mentioned: one value is a filter, several of the same dimension are a comparison
    filters: dict[str, list[str]] = {}
    for d, table in VALUE_WORDS.items():
        for value, words in table.items():
            for w in sorted(words, key=len, reverse=True):
                mm = _find(text, w)
                if mm:
                    filters.setdefault(d, [])
                    if value not in filters[d]:
                        filters[d].append(value)
                    text = _consume(text, mm)
                    break
    for code in COUNTRIES:
        mm = re.search(rf"(?<![\w-]){code}(?![\w-])", raw)
        if mm and code not in filters.get("country", []):
            filters.setdefault("country", []).append(code)
    if any(w in text for w in (" vs ", " versus ", " compared to ", " compare ", " against ")):
        for d, vals in filters.items():
            if len(vals) > 1 and d not in dims:
                dims.append(d)
    for d, vals in filters.items():
        if len(vals) > 1 and d not in dims:
            dims.append(d)

    if limit is None and dims:
        if ranked_low:
            order, limit = "metric_asc", 1
        elif ranked or which:
            order, limit = "metric_desc", 1

    if window is None:
        src = cat["sources"][cat["metrics"][metrics[0]]["source"]]
        if src.get("grain_floor") == "month":
            prev = ref.replace(day=1) - dt.timedelta(days=1)
            window = _month(prev.year, prev.month)
        else:
            window = (ref - dt.timedelta(days=int(cat["time"]["default_window_days"]) - 1), ref)
    start, end = window
    if start > ref:
        raise Unanswerable(f"The data runs through {ref.isoformat()}; that period has not happened yet.")
    end = min(end, ref)
    return MetricQuery(
        metrics=metrics,
        dimensions=dims,
        grain=grain,
        start=start,
        end=end,
        filters=[{"dimension": d, "values": v} for d, v in filters.items()],
        order=order,
        limit=limit,
    )
