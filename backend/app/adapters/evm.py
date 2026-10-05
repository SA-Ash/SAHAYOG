from datetime import datetime, timezone

from eth_utils import keccak

from app.adapters.base import ChainAdapter
from app.adapters.http import HttpCache
from app.adapters.storage import store_profile, store_transfers
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.intelligence import AddressProfile, Transfer
from app.services.validation import address_info, normalize_hash

TRANSFER_TOPIC = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
CHAIN_IDS = {"ethereum": 1, "bnb": 56, "polygon": 137}
NATIVE = {"ethereum": "ETH", "bnb": "BNB", "polygon": "POL"}


class EvmAdapter(ChainAdapter):
    def __init__(self, db, chain, http=None):
        self.db, self.chain = db, chain
        settings = get_settings()
        self.http = http or HttpCache(db, "etherscan-" + chain, settings.etherscan_url)

    async def call(self, module, action, **params):
        data, source = await self.http.request(
            "/v2/api",
            {
                "chainid": CHAIN_IDS[self.chain],
                "module": module,
                "action": action,
                "apikey": get_settings().etherscan_api_key,
                **params,
            },
        )
        result = data.get("result")
        if data.get("error"):
            raise AppError("PROVIDER_ERROR", "EVM provider rejected the request", 502)
        return result, source

    async def token_info(self, address):
        decimals, _ = await self.call(
            "proxy", "eth_call", to=address, data="0x313ce567", tag="latest"
        )
        symbol, _ = await self.call(
            "proxy", "eth_call", to=address, data="0x95d89b41", tag="latest"
        )
        try:
            precision = int(decimals, 16)
            from eth_abi import decode

            encoded = bytes.fromhex(symbol.removeprefix("0x"))
            name = (
                decode(["string"], encoded)[0]
                if len(encoded) > 32
                else encoded.rstrip(b"\0").decode()
            )
            return str(name)[:30], precision
        except (ValueError, TypeError, UnicodeError) as exc:
            raise AppError(
                "TOKEN_METADATA_UNAVAILABLE", "Cannot determine token precision", 502
            ) from exc

    async def get_tx(self, tx_hash):
        tx_hash = normalize_hash(self.chain, tx_hash)
        tx, source = await self.call("proxy", "eth_getTransactionByHash", txhash=tx_hash)
        if not tx:
            raise AppError("CHAIN_RECORD_NOT_FOUND", "Transaction not found", 404)
        receipt, _ = await self.call("proxy", "eth_getTransactionReceipt", txhash=tx_hash)
        if not receipt or int(receipt.get("status", "0x1"), 16) == 0:
            raise AppError("TRANSACTION_NOT_CONFIRMED", "Transaction pending or reverted", 422)
        block, _ = await self.call(
            "proxy", "eth_getBlockByNumber", tag=tx["blockNumber"], boolean="false"
        )
        timestamp = datetime.fromtimestamp(int(block["timestamp"], 16), timezone.utc)
        transfers = []
        if tx.get("to") and int(tx["value"], 16) > 0:
            transfers.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=tx_hash,
                    log_index=-1,
                    from_addr=tx["from"],
                    to_addr=tx["to"],
                    token=NATIVE[self.chain],
                    amount=str(int(tx["value"], 16)),
                    decimals=18,
                    block_time=timestamp,
                    source=source,
                )
            )
        for log in receipt.get("logs", []):
            topics = log.get("topics", [])
            if len(topics) != 3 or topics[0].lower() != TRANSFER_TOPIC:
                continue
            token, decimals = await self.token_info(log["address"])
            metadata = {"receipt_logs": receipt["logs"], "transaction_to": tx.get("to")}
            transfers.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=tx_hash,
                    log_index=int(log["logIndex"], 16),
                    from_addr="0x" + topics[1][-40:],
                    to_addr="0x" + topics[2][-40:],
                    token=token,
                    token_address=log["address"],
                    amount=str(int(log["data"], 16)),
                    decimals=decimals,
                    block_time=timestamp,
                    source=source,
                    metadata=metadata,
                )
            )
        internal, _ = await self.call("account", "txlistinternal", txhash=tx_hash)
        for index, row in enumerate(sorted(internal or [], key=lambda r: r.get("traceId", ""))):
            if row.get("isError") == "1" or not row.get("to") or int(row.get("value", "0")) == 0:
                continue
            transfers.append(
                Transfer(
                    chain=self.chain,
                    tx_hash=tx_hash,
                    log_index=-2 - index,
                    from_addr=row["from"],
                    to_addr=row["to"],
                    token=NATIVE[self.chain],
                    amount=row["value"],
                    decimals=18,
                    block_time=timestamp,
                    source=source,
                    metadata={"trace_id": row.get("traceId")},
                )
            )
        store_transfers(self.db, transfers)
        return transfers

    async def account_rows(self, address, action):
        rows = []
        for page in range(1, 21):
            result, _ = await self.call(
                "account",
                action,
                address=address,
                startblock=0,
                endblock=999999999,
                page=page,
                offset=100,
                sort="asc",
            )
            if not isinstance(result, list):
                if result in {None, "No transactions found"}:
                    break
                raise AppError("PROVIDER_ERROR", "Unexpected account response", 502)
            rows.extend(result)
            if len(result) < 100:
                return rows
        if len(rows) >= 2000:
            raise AppError(
                "TRANSFER_LIMIT_REACHED",
                "Provider history exceeds the demo limit; narrow the block range",
                422,
            )
        return rows

    async def get_transfers(self, address, direction="out", time_from=None, time_to=None):
        address = address_info(self.chain, address)[0]
        rows = []
        for action in ["tokentx", "txlist", "txlistinternal"]:
            rows.extend(await self.account_rows(address, action))
        hashes = sorted(
            {
                r["hash"]
                for r in rows
                if r.get("hash")
                and (not time_from or int(r["timeStamp"]) >= time_from.timestamp())
                and (not time_to or int(r["timeStamp"]) <= time_to.timestamp())
            }
        )
        transfers = []
        for tx_hash in hashes:
            transfers.extend(await self.get_tx(tx_hash))
        return [
            t
            for t in transfers
            if (direction == "both" and address in {t.from_addr, t.to_addr})
            or (direction == "out" and t.from_addr == address)
            or (direction == "in" and t.to_addr == address)
        ]

    async def get_profile(self, address):
        address = address_info(self.chain, address)[0]
        transactions = await self.account_rows(address, "txlist")
        incoming = [
            t
            for t in transactions
            if t.get("to", "").lower() == address
            and int(t.get("value", "0")) > 0
            and t.get("isError", "0") == "0"
        ]
        first_funding = min(incoming, key=lambda t: int(t["timeStamp"]), default=None)
        code, source = await self.call("proxy", "eth_getCode", address=address, tag="latest")
        times = [datetime.fromtimestamp(int(t["timeStamp"]), timezone.utc) for t in transactions]
        profile = AddressProfile(
            chain=self.chain,
            address=address,
            first_seen=min(times) if times else None,
            last_seen=max(times) if times else None,
            tx_count=len(transactions),
            gas_funder=first_funding["from"].lower() if first_funding else None,
            is_contract=code not in {"0x", "0x0", None},
            source=source,
            evidence=[
                {
                    "signal": "earliest_observed_native_funding",
                    "note": "Observed history is bounded; this is a funding heuristic, not proof of control",
                }
            ],
        )
        return store_profile(self.db, profile)

    async def get_balance(self, address, token=None):
        address = address_info(self.chain, address)[0]
        if token:
            token = address_info(self.chain, token)[0]
            amount, source = await self.call(
                "account", "tokenbalance", contractaddress=token, address=address, tag="latest"
            )
            symbol, decimals = await self.token_info(token)
        else:
            amount, source = await self.call("account", "balance", address=address, tag="latest")
            symbol, decimals = NATIVE[self.chain], 18
        return {
            "chain": self.chain,
            "address": address,
            "token": symbol,
            "amount": str(int(amount)),
            "decimals": decimals,
            "source": source,
            "evidence": [{"signal": "provider_balance"}],
        }
