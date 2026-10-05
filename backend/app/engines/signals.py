import math
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from statistics import median


def detect_sweeps(incoming, outgoing, window_seconds=300, minimum=3):
    grouped = defaultdict(list)
    used = set()
    for receipt in sorted(incoming, key=lambda t: t.block_time):
        outputs = [
            t
            for t in outgoing
            if (t.tx_hash, t.log_index) not in used
            and t.token == receipt.token
            and t.token_address == receipt.token_address
            and t.decimals == receipt.decimals
            and receipt.block_time
            <= t.block_time
            <= receipt.block_time + timedelta(seconds=window_seconds)
        ]
        if not outputs or len({t.to_addr for t in outputs}) != 1:
            continue
        received = int(receipt.amount)
        sent = sum(int(t.amount) for t in outputs)
        if received and received * 95 <= sent * 100 <= received * 105:
            target = outputs[0].to_addr
            grouped[(target, receipt.token, receipt.token_address, receipt.decimals)].append(
                (outputs[0].block_time - receipt.block_time).total_seconds()
            )
            used.update((t.tx_hash, t.log_index) for t in outputs)
    return [
        {
            "target": target,
            "token": token,
            "token_address": contract,
            "decimals": decimals,
            "count": len(delays),
            "median_delay_s": median(delays),
            "evidence": [
                {
                    "signal": "repeated_sweep",
                    "count": len(delays),
                    "window_seconds": window_seconds,
                    "ratio": ".95-1.05",
                }
            ],
        }
        for (target, token, contract, decimals), delays in grouped.items()
        if len(delays) >= minimum
    ]


def hub_metrics(incoming, outgoing, deposit_candidates):
    counterparties = len({t.from_addr for t in incoming})
    volumes = defaultdict(int)
    for t in incoming:
        volumes[(t.token, t.token_address, t.decimals)] += int(t.amount)
    return {
        "validated": counterparties >= 3 and len(set(deposit_candidates)) >= 3,
        "counterparties": counterparties,
        "deposit_candidates": len(set(deposit_candidates)),
        "volumes": [
            {"token": k[0], "contract": k[1], "decimals": k[2], "amount": str(v)}
            for k, v in volumes.items()
        ],
        "outgoing_count": len(outgoing),
    }


def mixer_score(incoming, outgoing, profiles=None):
    deposits = defaultdict(list)
    for t in incoming:
        deposits[(t.token, t.token_address, t.decimals, t.amount)].append(t)
    equal = max((len({t.from_addr for t in group}) for group in deposits.values()), default=0)
    depositors = len({t.from_addr for t in incoming})
    recipients = len({t.to_addr for t in outgoing})
    fresh = sum(
        1
        for t in outgoing
        if profiles and t.to_addr in profiles and profiles[t.to_addr].tx_count <= 2
    )
    score = min(
        1,
        (0.4 if equal >= 5 else 0)
        + (0.2 if depositors >= 8 else 0)
        + (0.2 if recipients >= 5 else 0)
        + (0.2 if fresh >= 5 else 0),
    )
    return {
        "score": score,
        "likely_mixer": score >= 0.8,
        "probabilistic": True,
        "evidence": [
            {
                "signal": "mixer_behaviour",
                "equal_denomination_depositors": equal,
                "unique_depositors": depositors,
                "withdrawal_recipients": recipients,
                "fresh_recipients": fresh,
                "note": "Behaviour is uncertain and does not establish direct output linkage",
            }
        ],
    }


class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a, b):
        self.parent[self.find(b)] = self.find(a)

    def groups(self):
        result = defaultdict(list)
        for value in list(self.parent):
            result[self.find(value)].append(value)
        return list(result.values())


def noisy_or(signals, verified=False):
    unique = {s["signal"]: max(0, min(1, float(s["weight"]))) for s in signals}
    value = 1 - math.prod(1 - weight for weight in unique.values())
    return min(1 if verified else 0.9, value)


def decayed_weight(weight, confirmed_at, now, tau):
    days = max(0, (now - confirmed_at).total_seconds() / 86400)
    return weight * math.exp(-days / tau)


def match_swap(
    amount, decimals, time, outputs, rate, fee="0", window=300, tolerance=".02", margin=".005"
):
    expected = Decimal(amount) / (Decimal(10) ** decimals) * Decimal(rate) * (1 - Decimal(fee))
    if expected <= 0:
        return None
    ranked = []
    for t in outputs:
        gap = (t.block_time - time).total_seconds()
        value = Decimal(t.amount) / (Decimal(10) ** t.decimals)
        error = abs(value - expected) / expected
        if 0 <= gap <= window and error <= Decimal(tolerance):
            ranked.append((float(error) + gap / window * 0.02, t))
    ranked.sort(key=lambda pair: pair[0])
    if not ranked or (len(ranked) > 1 and ranked[1][0] - ranked[0][0] < float(margin)):
        return None
    score, t = ranked[0]
    return {
        "transfer": t,
        "confidence": max(0, min(0.9, 1 - score)),
        "evidence": [
            {
                "signal": "swap_amount_time_match",
                "amount_error_and_time_score": score,
                "rate": str(rate),
                "fee": fee,
                "inferred": True,
            }
        ],
    }


def btc_change(transfers, profile_by_address):
    candidates = []
    for t in transfers:
        if t.metadata.get("coinjoin"):
            continue
        scripts = t.metadata.get("input_scripts", [])
        same = t.metadata.get("output_script") in scripts
        fresh = profile_by_address.get(t.to_addr) and profile_by_address[t.to_addr].tx_count <= 1
        other = [o for o in transfers if o.tx_hash == t.tx_hash and o.log_index != t.log_index]
        round_other = any(int(o.amount) % 100000 == 0 for o in other)
        if same and fresh and round_other:
            candidates.append(t.to_addr)
    return candidates


def fit_isotonic(samples):
    grouped = defaultdict(list)
    for score, outcome in samples:
        grouped[float(score)].append(int(outcome))
    blocks = []
    for score, values in sorted(grouped.items()):
        blocks.append([score, score, sum(values), len(values)])
        while len(blocks) > 1 and blocks[-2][2] / blocks[-2][3] > blocks[-1][2] / blocks[-1][3]:
            left, right = blocks[-2:]
            blocks[-2:] = [[left[0], right[1], left[2] + right[2], left[3] + right[3]]]
    return [
        {"from": a, "to": b, "probability": wins / count, "count": count}
        for a, b, wins, count in blocks
    ]


def calibrate(score, bins):
    if not bins:
        return score
    eligible = [b for b in bins if b["from"] <= score]
    return eligible[-1]["probability"] if eligible else bins[0]["probability"]
