from collections import defaultdict, deque
from decimal import Decimal


def allocate(amount, weights):
    weights = {key: int(value) for key, value in weights.items() if int(value) > 0}
    total = sum(weights.values())
    if not total or not amount:
        return {}
    result = {key: amount * value // total for key, value in weights.items()}
    remainder = amount - sum(result.values())
    ordered = sorted(weights, key=lambda key: (-(amount * weights[key] % total), str(key)))
    for key in ordered[:remainder]:
        result[key] += 1
    return {key: value for key, value in result.items() if value}


class Ledger:
    def __init__(self, method="haircut", cutoff=Decimal(".02")):
        self.method, self.cutoff = method, Decimal(cutoff)
        self.accounts = defaultdict(deque)
        self.uncertain = defaultdict(lambda: defaultdict(int))
        self.exposure = defaultdict(set)
        self.edges = []
        self.unknown_funding = []
        self.snapshots = []

    def balance(self, key):
        result = defaultdict(int)
        for colour, amount in self.accounts[key]:
            result[colour] += amount
        return dict(result)

    def credit(self, key, portions):
        for colour, amount in portions.items():
            if amount > 0:
                self.accounts[key].append((colour, amount))
                if colour:
                    self.exposure[key].add(colour)

    def debit(self, key, amount):
        total = sum(self.balance(key).values())
        if total < amount:
            self.credit(key, {None: amount - total})
            self.unknown_funding.append(
                {
                    "account": list(key),
                    "amount": str(amount - total),
                    "signal": "unobserved_opening_funds",
                }
            )
        if self.method == "fifo":
            result = defaultdict(int)
            remaining = amount
            while remaining:
                colour, value = self.accounts[key].popleft()
                used = min(value, remaining)
                result[colour] += used
                remaining -= used
                if value > used:
                    self.accounts[key].appendleft((colour, value - used))
            return dict(result)
        balances = self.balance(key)
        result = allocate(amount, balances)
        self.accounts[key].clear()
        self.credit(key, {key: value - result.get(key, 0) for key, value in balances.items()})
        return result

    def move(
        self, sender, receiver, amount, at, identity, origin=None, mixer=False, output_amount=None
    ):
        amount = int(amount)
        portions = self.debit(sender, amount)
        if origin:
            colour, reported = origin
            coloured = min(amount, int(reported))
            portions = {colour: coloured, None: amount - coloured}
        output = int(output_amount) if output_amount is not None else amount
        portions = allocate(output, portions)
        original = dict(portions)
        if mixer:
            for colour, value in portions.items():
                if colour:
                    self.uncertain[receiver][colour] += value
            portions = {None: output}
        else:
            dropped = 0
            for colour, value in list(portions.items()):
                if colour and output and Decimal(value) / Decimal(output) < self.cutoff:
                    dropped += value
                    self.uncertain[receiver][colour] += value
                    del portions[colour]
            portions[None] = portions.get(None, 0) + dropped
        self.credit(receiver, portions)
        self.exposure[receiver].update(self.exposure[sender])
        if origin:
            self.exposure[receiver].add(origin[0])
        edge = {
            "id": identity,
            "source": list(sender),
            "target": list(receiver),
            "amount": str(output),
            "by_case": {key: str(value) for key, value in portions.items() if key},
            "uncoloured": str(portions.get(None, 0)),
            "uncertain": mixer,
            "at": at.isoformat(),
            "poison_upper_bounds": {key: str(output) for key in self.exposure[receiver]}
            if self.method == "poison"
            else {},
        }
        self.edges.append(edge)
        self.snapshots.append(
            {
                "account": list(receiver),
                "at": at.isoformat(),
                "balance": {
                    key or "uncoloured": str(value) for key, value in self.balance(receiver).items()
                },
                "uncertain": {key: str(value) for key, value in self.uncertain[receiver].items()},
                "incoming": original,
            }
        )
        assert sum(self.balance(receiver).values()) >= output
        assert sum(int(v) for v in edge["by_case"].values()) + int(edge["uncoloured"]) == output
        return edge

    def result(self):
        return [
            {
                "chain": key[0],
                "address": key[1],
                "token": key[2],
                "token_address": key[3],
                "decimals": key[4],
                "total_balance": str(sum(self.balance(key).values())),
                "by_case": {
                    colour: str(value) for colour, value in self.balance(key).items() if colour
                },
                "uncoloured": str(self.balance(key).get(None, 0)),
                "uncertain_by_case": {
                    colour: str(value) for colour, value in self.uncertain[key].items()
                },
                "poison_upper_bounds": {
                    colour: str(sum(self.balance(key).values())) for colour in self.exposure[key]
                }
                if self.method == "poison"
                else {},
            }
            for key in self.accounts
        ]
