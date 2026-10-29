"""A simulated European neobroker: customers, instruments, prices, trades, savings plans and cash.

Deterministic for config/world.yaml's seed: `World().day(i)` always returns the same batches,
so ingestion can be replayed, a day can arrive late in a drill, and tests can rebuild a lake.
Nothing here is real customer data; names, IBANs and e-mails are generated.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pyarrow as pa

from apr import conf
from apr.contracts.model import by_name

FIRST = [
    "Anna",
    "Lukas",
    "Marie",
    "Paul",
    "Sofia",
    "Leon",
    "Emma",
    "Jonas",
    "Lea",
    "Noah",
    "Chiara",
    "Luca",
    "Giulia",
    "Hugo",
    "Inès",
    "Mateo",
    "Lucía",
    "Lars",
    "Elin",
    "Sanne",
    "Pieter",
    "Joana",
    "Tiago",
    "Aoife",
    "Eero",
    "Nikos",
    "Jana",
    "Matej",
    "Rūta",
    "Kārlis",
    "Kadri",
    "Camille",
]
LAST = [
    "Müller",
    "Schmidt",
    "Weber",
    "Wagner",
    "Rossi",
    "Bianchi",
    "García",
    "Martin",
    "Bernard",
    "Novak",
    "Jansen",
    "de Vries",
    "Silva",
    "Santos",
    "Murphy",
    "Virtanen",
    "Papadopoulos",
    "Horváth",
    "Kazlauskas",
    "Bērziņš",
    "Tamm",
    "Peeters",
    "Lindqvist",
    "Gruber",
    "Moreau",
]
SYL = [
    "nor",
    "vel",
    "tek",
    "sol",
    "mar",
    "ion",
    "lux",
    "dra",
    "ver",
    "kon",
    "alp",
    "ost",
    "rhe",
    "fin",
    "gal",
    "zen",
    "or",
    "ast",
    "bel",
    "cor",
]
IBAN_LEN = {
    "DE": 22,
    "FR": 27,
    "IT": 27,
    "ES": 24,
    "AT": 20,
    "NL": 18,
    "SE": 24,
    "BE": 16,
    "PT": 25,
    "IE": 22,
    "FI": 18,
    "GR": 27,
    "SK": 24,
    "SI": 19,
    "LT": 20,
    "LV": 21,
    "EE": 20,
    "LU": 20,
}
EPOCH = dt.datetime(1970, 1, 1)


def _alnum_to_digits(s: str) -> str:
    return "".join(str(int(ch, 36)) for ch in s)


def iban(country: str, bban: str) -> str:
    """An IBAN with a correct ISO 13616 check (mod 97), so validators accept it."""
    check = 98 - int(_alnum_to_digits(bban + country + "00")) % 97
    return f"{country}{check:02d}{bban}"


def isin(prefix: str, body: str) -> str:
    """An ISIN with a correct ISO 6166 check digit (Luhn over the digit expansion)."""
    digits = _alnum_to_digits(prefix + body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 0:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return f"{prefix}{body}{(10 - total % 10) % 10}"


def _ts(days: np.ndarray, base: dt.date) -> np.ndarray:
    """Fractional days after `base` as microsecond timestamps."""
    origin = (dt.datetime.combine(base, dt.time()) - EPOCH).total_seconds()
    return ((origin + days * 86400.0) * 1e6).astype("int64").astype("datetime64[us]")


@dataclass(frozen=True)
class Customers:
    ids: np.ndarray
    country: np.ndarray
    segment: np.ndarray
    signup_day: np.ndarray  # days after start (negative = before)
    card: np.ndarray
    balance: np.ndarray
    table: pa.Table


class World:
    def __init__(self, cfg: dict | None = None) -> None:
        self.cfg = cfg or conf.read("world")
        self.seed = int(self.cfg["seed"])
        self.start: dt.date = self.cfg["start"]
        self.days = int(self.cfg["days"])

    def rng(self, *key: int | str) -> np.random.Generator:
        parts = [self.seed] + [k if isinstance(k, int) else int.from_bytes(k.encode(), "little") % 2**31 for k in key]
        return np.random.default_rng(parts)

    def date(self, i: int) -> dt.date:
        return self.start + dt.timedelta(days=i)

    # ---- static entities -------------------------------------------------------------
    @cached_property
    def customers(self) -> Customers:
        c = self.cfg
        n = int(c["customers"])
        r = self.rng("customers")
        countries = list(c["countries"])
        p = np.array([c["countries"][k] for k in countries], dtype=float)
        country = r.choice(countries, n, p=p / p.sum())
        segs = list(c["segments"])
        sp = np.array([c["segments"][s]["share"] for s in segs])
        segment = r.choice(segs, n, p=sp / sp.sum())
        before = r.random(n) < c["signed_up_before_start"]
        signup_day = np.where(before, -r.uniform(1, 3 * 365, n), r.uniform(0, self.days, n))
        ids = np.array([f"C{1000000 + i}" for i in range(n)])
        first = r.choice(FIRST, n)
        last = r.choice(LAST, n)
        names = np.char.add(np.char.add(first, " "), last)
        emails = np.array(
            [
                f"{f.lower()}.{l.lower().replace(' ', '')}{i % 997}@example.org"
                for i, (f, l) in enumerate(zip(first, last))
            ]
        )
        ibans = np.array([iban(ct, "".join(str(d) for d in r.integers(0, 10, IBAN_LEN[ct] - 4))) for ct in country])
        age_days = r.uniform(18 * 365.25, 75 * 365.25, n)
        birth = np.array([self.start - dt.timedelta(days=float(a)) for a in age_days], dtype="datetime64[D]")
        chans = list(c["acquisition_channels"])
        cp = np.array([c["acquisition_channels"][k] for k in chans])
        channel = r.choice(chans, n, p=cp / cp.sum())
        card = r.random(n) < c["cash"]["card_holders"]
        balance = r.lognormal(np.log(c["cash"]["balance_median_eur"]), 1.0, n)
        table = pa.table(
            {
                "customer_id": ids,
                "full_name": names,
                "email": emails,
                "iban": ibans,
                "birth_date": pa.array(birth, pa.date32()),
                "country": country,
                "signup_at": pa.array(_ts(signup_day, self.start), pa.timestamp("us")),
                "channel": channel,
                "segment": segment,
            },
            schema=by_name("customers").arrow_schema(),
        )
        return Customers(ids, country, segment, signup_day, card, balance, table)

    @cached_property
    def instruments(self) -> pa.Table:
        r = self.rng("instruments")
        rows: dict[str, list] = {"isin": [], "name": [], "asset_class": [], "currency": []}
        legal = {
            "stock": ["AG", "SE", "NV", "SA", "Inc.", "plc"],
            "etf": ["UCITS ETF"],
            "bond": ["Bond"],
            "crypto": [""],
        }
        prefixes = {"stock": ["DE", "US", "FR", "NL", "IT"], "etf": ["IE", "LU"], "bond": ["DE", "FR", "XS"]}
        seen = set()
        for cls, count in self.cfg["instruments"].items():
            for i in range(int(count)):
                word = "".join(r.choice(SYL, 2)).capitalize()
                if cls == "crypto":
                    code = f"{word[:3].upper()}{i:02d}"
                    code_isin = isin("XF", f"000{code}"[:9].ljust(9, "0"))
                    name = f"{word} (crypto)"
                    cur = "EUR"
                else:
                    pre = str(r.choice(prefixes[cls]))
                    code_isin = isin(pre, "".join(str(d) for d in r.integers(0, 10, 9)))
                    name = {
                        "stock": f"{word} {r.choice(legal['stock'])}",
                        "etf": f"{word} {r.choice(['World', 'Europe', 'S&P 500', 'EM', 'Tech'])} UCITS ETF",
                        "bond": f"{word} {r.integers(1, 9)}.{r.integers(0, 9)}% {2027 + int(r.integers(0, 15))}",
                    }[cls]
                    cur = "USD" if pre == "US" else "EUR"
                if code_isin in seen:
                    continue
                seen.add(code_isin)
                rows["isin"].append(code_isin)
                rows["name"].append(name)
                rows["asset_class"].append(cls)
                rows["currency"].append(cur)
        return pa.table(rows, schema=by_name("instruments").arrow_schema())

    @cached_property
    def prices(self) -> np.ndarray:
        """Close in EUR, shape (instruments, days)."""
        r = self.rng("prices")
        cls = self.instruments.column("asset_class").to_numpy(zero_copy_only=False)
        vol = np.array([self.cfg["annual_volatility"][c] for c in cls]) / np.sqrt(252)
        start = np.where(
            cls == "crypto",
            r.lognormal(np.log(800), 2.0, len(cls)),
            np.where(cls == "bond", r.normal(98, 4, len(cls)), r.lognormal(np.log(60), 0.9, len(cls))),
        )
        steps = r.normal(0.0002, 1.0, (len(cls), self.days)) * vol[:, None]
        return np.maximum(0.01, start[:, None] * np.exp(np.cumsum(steps, axis=1)))

    @cached_property
    def popularity(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        """Per asset class: instrument indices and Zipf-like weights (a few names get most trades)."""
        r = self.rng("popularity")
        cls = self.instruments.column("asset_class").to_numpy(zero_copy_only=False)
        out = {}
        for c in self.cfg["instruments"]:
            idx = np.flatnonzero(cls == c)
            idx = r.permutation(idx)
            w = 1.0 / np.arange(1, len(idx) + 1) ** 1.1
            out[c] = (idx, w / w.sum())
        return out

    @cached_property
    def plans(self) -> dict[str, np.ndarray]:
        cu = self.customers
        sp = self.cfg["savings_plans"]
        r = self.rng("plans")
        share = np.array([sp["share_of_customers"][s] for s in cu.segment])
        has = r.random(len(cu.ids)) < share
        k = np.where(has, r.choice(sp["plans_per_customer"], len(cu.ids), p=sp["plans_weights"]), 0)
        owner = np.repeat(np.arange(len(cu.ids)), k)
        m = len(owner)
        created = np.maximum(cu.signup_day[owner] + r.uniform(0, 60, m), cu.signup_day[owner] + 0.01)
        idx, w = self.popularity["etf"]
        isin_idx = np.where(r.random(m) < 0.85, r.choice(idx, m, p=w), r.choice(self.popularity["stock"][0], m))
        amount = np.round(np.clip(r.lognormal(np.log(sp["amount_eur_median"]), 0.7, m), 1, 5000) / 5) * 5
        amount = np.maximum(amount, 1.0)
        ints = list(sp["interval"])
        interval = r.choice(ints, m, p=np.array([sp["interval"][x] for x in ints]))
        exec_day = r.choice(sp["execution_days"], m)
        months_alive = r.geometric(sp["monthly_cancel_rate"], m)
        cancelled = created + months_alive * 30.4 + r.uniform(0, 30, m)
        return {
            "owner": owner,
            "created": created,
            "isin": isin_idx,
            "amount": amount,
            "interval": interval,
            "exec_day": exec_day,
            "cancelled": cancelled,
            "id": np.array([f"P{2000000 + i}" for i in range(m)]),
        }

    # ---- daily batches ------------------------------------------------------------------
    def customers_asof(self, i: int) -> pa.Table:
        mask = self.customers.signup_day < i + 1
        return self.customers.table.filter(pa.array(mask))

    def savings_plans_asof(self, i: int) -> pa.Table:
        p = self.plans
        alive = p["created"] < i + 1
        cancelled_at = np.where(p["cancelled"] < i + 1, p["cancelled"], np.nan)
        sel = np.flatnonzero(alive)
        canc = cancelled_at[sel]
        canc_ts = pa.array(np.where(np.isnan(canc), 0, canc), pa.float64())
        isins = self.instruments.column("isin").to_numpy(zero_copy_only=False)
        return pa.table(
            {
                "plan_id": p["id"][sel],
                "customer_id": self.customers.ids[p["owner"][sel]],
                "isin": isins[p["isin"][sel]],
                "amount_eur": p["amount"][sel],
                "interval": p["interval"][sel],
                "created_at": pa.array(_ts(p["created"][sel], self.start), pa.timestamp("us")),
                "cancelled_at": pa.array(
                    [None if np.isnan(x) else v for x, v in zip(canc, _ts(canc_ts.to_numpy(), self.start))],
                    pa.timestamp("us"),
                ),
            },
            schema=by_name("savings_plans").arrow_schema(),
        )

    def prices_on(self, i: int) -> pa.Table:
        d = self.date(i)
        cls = self.instruments.column("asset_class").to_numpy(zero_copy_only=False)
        open_ = np.ones(len(cls), bool) if d.weekday() < 5 else cls == "crypto"
        return pa.table(
            {
                "isin": self.instruments.column("isin").filter(pa.array(open_)),
                "price_date": pa.array([d] * int(open_.sum()), pa.date32()),
                "close_eur": np.round(self.prices[open_, i], 4),
            },
            schema=by_name("prices_daily").arrow_schema(),
        )

    def _plan_runs(self, i: int) -> np.ndarray:
        d = self.date(i)
        p = self.plans
        alive = (p["created"] < i) & (p["cancelled"] > i)
        if d.weekday() >= 5:
            return np.array([], dtype=int)
        due = np.zeros(len(alive), bool)
        due |= (p["interval"] == "weekly") & (d.weekday() == 0)
        two_or_16 = np.isin(d.day, self.cfg["savings_plans"]["execution_days"])
        due |= (p["interval"] == "biweekly") & two_or_16
        due |= (p["interval"] == "monthly") & (p["exec_day"] == d.day)
        due |= (p["interval"] == "quarterly") & (p["exec_day"] == d.day) & (d.month in (1, 4, 7, 10))
        return np.flatnonzero(alive & due)

    def trades_on(self, i: int) -> pa.Table:
        c = self.cfg
        d = self.date(i)
        r = self.rng("trades", i)
        cu = self.customers
        isins = self.instruments.column("isin").to_numpy(zero_copy_only=False)
        cls_of = self.instruments.column("asset_class").to_numpy(zero_copy_only=False)
        signed = cu.signup_day < i
        weekday = d.weekday() < 5
        cols: dict[str, list[np.ndarray]] = {
            k: [] for k in ["cust", "inst", "time", "side", "amount", "channel", "fee"]
        }
        for seg, sc in c["segments"].items():
            pool = np.flatnonzero(signed & (cu.segment == seg))
            rate = sc["trade_rate"] if weekday else sc["trade_rate"] * sc["mix"]["crypto"]
            who = pool[r.random(len(pool)) < rate]
            n = len(who)
            if n == 0:
                continue
            classes = list(sc["mix"]) if weekday else ["crypto"]
            mp = np.array([sc["mix"][k] for k in classes])
            cls = r.choice(classes, n, p=mp / mp.sum())
            inst = np.empty(n, int)
            for k in classes:
                m = cls == k
                idx, w = self.popularity[k]
                inst[m] = r.choice(idx, int(m.sum()), p=w)
            hours = r.normal(14.5, 3.5, n).clip(7.5, 22.9) if weekday else r.uniform(0, 23.99, n)
            cols["cust"].append(who)
            cols["inst"].append(inst)
            cols["time"].append(hours / 24.0)
            cols["side"].append(np.where(r.random(n) < c["buy_share"], "buy", "sell"))
            cols["amount"].append(np.round(r.lognormal(np.log(sc["ticket_eur"]), 0.9, n).clip(1, 250000), 2))
            ch = list(c["trade_channels"])
            cols["channel"].append(r.choice(ch, n, p=[c["trade_channels"][x] for x in ch]))
            cols["fee"].append(np.full(n, c["order_fee_eur"]))
        runs = self._plan_runs(i)
        if len(runs):
            p = self.plans
            n = len(runs)
            cols["cust"].append(p["owner"][runs])
            cols["inst"].append(p["isin"][runs])
            cols["time"].append(np.full(n, 9.5 / 24.0) + r.uniform(0, 0.02, n))
            cols["side"].append(np.full(n, "buy"))
            cols["amount"].append(p["amount"][runs].astype(float))
            cols["channel"].append(np.full(n, "savings_plan"))
            cols["fee"].append(np.zeros(n))
        cust = np.concatenate(cols["cust"]).astype(int)
        inst = np.concatenate(cols["inst"]).astype(int)
        amount = np.concatenate(cols["amount"])
        n = len(cust)
        price = np.round(self.prices[inst, i] * (1 + r.normal(0, 0.002, n)), 4)
        order = np.argsort(np.concatenate(cols["time"]), kind="stable")
        venues = list(c["venues"])
        tbl = pa.table(
            {
                "trade_id": np.array([f"T{i:03d}{k:07d}" for k in range(n)]),
                "customer_id": cu.ids[cust][order],
                "isin": isins[inst][order],
                "executed_at": pa.array(_ts(i + np.concatenate(cols["time"])[order], self.start), pa.timestamp("us")),
                "side": np.concatenate(cols["side"])[order],
                "quantity": np.round(amount / price, 6)[order],
                "price_eur": price[order],
                "amount_eur": amount[order],
                "fee_eur": np.concatenate(cols["fee"])[order],
                "channel": np.concatenate(cols["channel"])[order],
                "venue": r.choice(venues, n, p=[c["venues"][v] for v in venues]),
            },
            schema=by_name("trades").arrow_schema(),
        )
        _ = cls_of
        return tbl

    def cash_on(self, i: int) -> pa.Table:
        c = self.cfg["cash"]
        d = self.date(i)
        r = self.rng("cash", i)
        cu = self.customers
        signed = np.flatnonzero(cu.signup_day < i)
        parts: list[tuple[np.ndarray, str, np.ndarray]] = []
        dep = signed[r.random(len(signed)) < c["deposits_per_month"] / 30.4]
        parts.append((dep, "deposit", np.round(r.lognormal(np.log(c["deposit_median_eur"]), 0.9, len(dep)), 2)))
        wd = signed[r.random(len(signed)) < c["withdrawals_per_month"] / 30.4]
        parts.append((wd, "withdrawal", -np.round(r.lognormal(np.log(c["withdrawal_median_eur"]), 0.9, len(wd)), 2)))
        holders = signed[cu.card[signed]]
        sp = holders[r.random(len(holders)) < c["card_spend_per_day"]]
        parts.append((sp, "card_spend", -np.round(r.lognormal(np.log(c["card_spend_median_eur"]), 0.8, len(sp)), 2)))
        if d.day == 1:
            parts.append((signed, "interest", np.round(cu.balance[signed] * c["interest_rate"] / 12, 2)))
            # saveback on last month's card spend, from the simulated spend rate (capped)
            est = c["card_spend_per_day"] * 30.4 * c["card_spend_median_eur"] * r.lognormal(0, 0.3, len(holders))
            parts.append(
                (holders, "saveback", np.round(np.minimum(est * c["saveback_rate"], c["saveback_cap_eur"]), 2))
            )
        who = np.concatenate([p[0] for p in parts]).astype(int)
        kind = np.concatenate([np.full(len(p[0]), p[1]) for p in parts])
        amt = np.concatenate([p[2] for p in parts])
        n = len(who)
        t = r.uniform(0, 0.999, n)
        t[kind == "interest"] = 0.01
        t[kind == "saveback"] = 0.02
        order = np.argsort(t, kind="stable")
        return pa.table(
            {
                "movement_id": np.array([f"M{i:03d}{k:07d}" for k in range(n)]),
                "customer_id": cu.ids[who][order],
                "booked_at": pa.array(_ts(i + t[order], self.start), pa.timestamp("us")),
                "type": kind[order],
                "amount_eur": amt[order],
            },
            schema=by_name("cash_movements").arrow_schema(),
        )

    def day(self, i: int) -> dict[str, pa.Table]:
        """Everything that lands for day i (the batch loaded the morning after)."""
        return {
            "raw.customers": self.customers_asof(i),
            "raw.instruments": self.instruments,
            "raw.savings_plans": self.savings_plans_asof(i),
            "raw.prices_daily": self.prices_on(i),
            "raw.trades": self.trades_on(i),
            "raw.cash_movements": self.cash_on(i),
        }
