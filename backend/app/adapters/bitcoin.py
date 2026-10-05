from collections import Counter
from datetime import datetime, timezone

from app.adapters.base import ChainAdapter
from app.adapters.http import HttpCache
from app.adapters.storage import store_profile, store_transfers
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.intelligence import AddressProfile, Transfer
from app.services.validation import address_info, normalize_hash


class BitcoinAdapter(ChainAdapter):
    def __init__(self, db, http=None):
        self.db = db
        self.http = http or HttpCache(db, "esplora", get_settings().bitcoin_url)

    def normalize(self, tx, source):
        if not tx["status"].get("confirmed"):
            return []
        inputs = [
            v["prevout"]
            for v in tx["vin"]
            if v.get("prevout") and v["prevout"].get("scriptpubkey_address")
        ]
        owners = sorted({v["scriptpubkey_address"] for v in inputs})
        if not owners:
            return []
        timestamp = datetime.fromtimestamp(tx["status"]["block_time"], timezone.utc)
        total = sum(v["value"] for v in inputs)
        denominations = Counter(
            out["value"]
            for out in tx["vout"]
            if out.get("scriptpubkey_address") and out["value"] > 0
        )
        coinjoin = len(owners) >= 3 and max(denominations.values(), default=0) >= 3
        transfers = []
        for index, out in enumerate(tx["vout"]):
            if not out.get("scriptpubkey_address") or out["value"] == 0:
                continue
            transfers.append(
                Transfer(
                    chain="bitcoin",
                    tx_hash=tx["txid"],
                    log_index=index,
                    from_addr=owners[0],
                    to_addr=out["scriptpubkey_address"],
                    token="BTC",
                    amount=str(out["value"]),
                    decimals=8,
                    block_time=timestamp,
                    source=source,
                    metadata={
                        "input_addresses": owners,
                        "input_total": str(total),
                        "fee": str(tx.get("fee", 0)),
                        "input_scripts": list({v["scriptpubkey_type"] for v in inputs}),
                        "output_script": out.get("scriptpubkey_type"),
                        "joint_input": len(owners) > 1,
                        "coinjoin": coinjoin,
                        "attribution": "Joint input set; representative sender is not individual ownership proof",
                    },
                    evidence=[
                        {
                            "signal": "utxo_output",
                            "note": "No per-input flow allocation is asserted",
                        }
                    ],
                )
            )
        return transfers

    async def get_tx(self, tx_hash):
        tx, source = await self.http.request("/tx/" + normalize_hash("bitcoin", tx_hash))
        if not tx["status"].get("confirmed"):
            raise AppError("TRANSACTION_NOT_CONFIRMED", "Bitcoin transaction is unconfirmed", 422)
        transfers = self.normalize(tx, source)
        store_transfers(self.db, transfers)
        return transfers

    async def get_transfers(self, address, direction="out", time_from=None, time_to=None):
        address = address_info("bitcoin", address)[0]
        transfers = []
        path = f"/address/{address}/txs"
        for page in range(20):
            rows, source = await self.http.request(path)
            confirmed = [t for t in rows if t["status"].get("confirmed")]
            for row in confirmed:
                transfers.extend(self.normalize(row, source))
            if len(confirmed) < 25 or (
                time_from and confirmed[-1]["status"]["block_time"] < time_from.timestamp()
            ):
                break
            if page == 19:
                raise AppError(
                    "TRANSFER_LIMIT_REACHED", "Bitcoin history exceeds the demo limit", 422
                )
            path = f"/address/{address}/txs/chain/{confirmed[-1]['txid']}"
        unique = {(t.tx_hash, t.log_index): t for t in transfers}
        result = [
            t
            for t in unique.values()
            if (not time_from or t.block_time >= time_from)
            and (not time_to or t.block_time <= time_to)
            and (
                (direction in {"out", "both"} and address in t.metadata["input_addresses"])
                or (direction in {"in", "both"} and t.to_addr == address)
            )
        ]
        store_transfers(self.db, result)
        return result

    async def get_profile(self, address):
        address = address_info("bitcoin", address)[0]
        info, source = await self.http.request("/address/" + address)
        txs = await self.get_transfers(address, "both")
        profile = AddressProfile(
            chain="bitcoin",
            address=address,
            first_seen=min((t.block_time for t in txs), default=None),
            last_seen=max((t.block_time for t in txs), default=None),
            tx_count=info["chain_stats"]["tx_count"],
            is_contract=False,
            source=source,
            evidence=[{"signal": "utxo_history", "note": "No gas funder exists on Bitcoin"}],
        )
        return store_profile(self.db, profile)

    async def get_balance(self, address, token=None):
        if token not in {None, "BTC"}:
            raise AppError("UNSUPPORTED_TOKEN", "Bitcoin adapter supports BTC", 422)
        data, source = await self.http.request("/address/" + address_info("bitcoin", address)[0])
        stats = data["chain_stats"]
        return {
            "chain": "bitcoin",
            "address": address,
            "amount": str(stats["funded_txo_sum"] - stats["spent_txo_sum"]),
            "token": "BTC",
            "decimals": 8,
            "source": source,
            "evidence": [{"signal": "confirmed_utxo_balance"}],
        }
