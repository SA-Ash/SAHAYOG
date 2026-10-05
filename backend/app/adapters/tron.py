from datetime import datetime, timezone

import base58

from app.adapters.base import ChainAdapter
from app.adapters.evm import TRANSFER_TOPIC
from app.adapters.http import HttpCache
from app.adapters.storage import store_profile, store_transfers
from app.core.config import get_settings
from app.core.errors import AppError
from app.schemas.intelligence import AddressProfile, Transfer
from app.services.validation import address_info, normalize_hash


def tron_address(value):
    if value.startswith("T"):
        return address_info("tron", value)[0]
    raw = bytes.fromhex(value.removeprefix("0x"))
    if len(raw) == 20:
        raw = b"\x41" + raw
    return address_info("tron", base58.b58encode_check(raw).decode())[0]


def tron_hex(value):
    return base58.b58decode_check(address_info("tron", value)[0]).hex()


class TronAdapter(ChainAdapter):
    def __init__(self, db, http=None):
        settings = get_settings()
        self.db = db
        headers = {"TRON-PRO-API-KEY": settings.tron_api_key} if settings.tron_api_key else {}
        self.http = http or HttpCache(db, "trongrid", settings.tron_url, headers)

    async def pages(self, path, params):
        rows = []
        for _ in range(20):
            data, source = await self.http.request(path, params)
            rows.extend(data.get("data", []))
            fingerprint = data.get("meta", {}).get("fingerprint")
            if not fingerprint:
                return rows, source
            params = {**params, "fingerprint": fingerprint}
        raise AppError(
            "TRANSFER_LIMIT_REACHED",
            "Tron history exceeds the demo limit; narrow the time window",
            422,
        )

    async def token_info(self, address, owner):
        values = []
        for selector in ["symbol()", "decimals()"]:
            data, _ = await self.http.request(
                "/wallet/triggerconstantcontract",
                method="POST",
                body={
                    "owner_address": owner,
                    "contract_address": address,
                    "function_selector": selector,
                    "visible": True,
                },
            )
            if not data.get("constant_result"):
                raise AppError(
                    "TOKEN_METADATA_UNAVAILABLE", "Cannot determine TRC20 token metadata", 502
                )
            values.append(data["constant_result"][0])
        from eth_abi import decode

        raw = bytes.fromhex(values[0])
        symbol = decode(["string"], raw)[0] if len(raw) > 32 else raw.rstrip(b"\0").decode()
        return str(symbol)[:30], int(values[1], 16)

    async def get_tx(self, tx_hash):
        tx_hash = normalize_hash("tron", tx_hash)
        tx, source = await self.http.request(
            "/wallet/gettransactionbyid", method="POST", body={"value": tx_hash}
        )
        info, _ = await self.http.request(
            "/wallet/gettransactioninfobyid", method="POST", body={"value": tx_hash}
        )
        if not tx or not info:
            raise AppError("CHAIN_RECORD_NOT_FOUND", "Confirmed Tron transaction not found", 404)
        if info.get("receipt", {}).get("result") not in {None, "SUCCESS"} or any(
            r.get("contractRet") != "SUCCESS" for r in tx.get("ret", [])
        ):
            raise AppError("TRANSACTION_REVERTED", "Tron transaction failed", 422)
        timestamp = datetime.fromtimestamp(info["blockTimeStamp"] / 1000, timezone.utc)
        transfers = []
        owner = None
        for index, contract in enumerate(tx["raw_data"].get("contract", [])):
            value = contract["parameter"]["value"]
            if value.get("owner_address"):
                owner = tron_address(value["owner_address"])
            if contract["type"] == "TransferContract":
                transfers.append(
                    Transfer(
                        chain="tron",
                        tx_hash=tx_hash,
                        log_index=-1 - index,
                        from_addr=owner,
                        to_addr=tron_address(value["to_address"]),
                        token="TRX",
                        amount=str(value["amount"]),
                        decimals=6,
                        block_time=timestamp,
                        source=source,
                    )
                )
        for index, log in enumerate(info.get("log", [])):
            topics = log.get("topics", [])
            if len(topics) != 3 or topics[0].removeprefix(
                "0x"
            ).lower() != TRANSFER_TOPIC.removeprefix("0x"):
                continue
            address = tron_address(log["address"])
            token, decimals = await self.token_info(address, owner)
            transfers.append(
                Transfer(
                    chain="tron",
                    tx_hash=tx_hash,
                    log_index=index,
                    from_addr=tron_address(topics[1][-40:]),
                    to_addr=tron_address(topics[2][-40:]),
                    token=token,
                    token_address=address,
                    amount=str(int(log["data"], 16)),
                    decimals=decimals,
                    block_time=timestamp,
                    source=source,
                )
            )
        store_transfers(self.db, transfers)
        return transfers

    async def get_transfers(self, address, direction="out", time_from=None, time_to=None):
        address = address_info("tron", address)[0]
        params = {"limit": 200, "only_confirmed": "true", "order_by": "block_timestamp,asc"}
        if time_from:
            params["min_timestamp"] = int(time_from.timestamp() * 1000)
        if time_to:
            params["max_timestamp"] = int(time_to.timestamp() * 1000)
        if direction in {"in", "out"}:
            params["only_to" if direction == "in" else "only_from"] = "true"
        rows, _ = await self.pages(f"/v1/accounts/{address}/transactions/trc20", params)
        hashes = {r["transaction_id"] for r in rows}
        native, _ = await self.pages(f"/v1/accounts/{address}/transactions", params)
        hashes.update(r["txID"] for r in native)
        transfers = []
        for tx_hash in sorted(hashes):
            transfers.extend(await self.get_tx(tx_hash))
        return [
            t
            for t in transfers
            if (not time_from or t.block_time >= time_from)
            and (not time_to or t.block_time <= time_to)
            and (
                (direction == "out" and t.from_addr == address)
                or (direction == "in" and t.to_addr == address)
                or (direction == "both" and address in {t.from_addr, t.to_addr})
            )
        ]

    async def get_profile(self, address):
        address = address_info("tron", address)[0]
        account, source = await self.http.request(
            "/wallet/getaccount", method="POST", body={"address": address, "visible": True}
        )
        rows, _ = await self.pages(
            f"/v1/accounts/{address}/transactions",
            {"limit": 200, "only_confirmed": "true", "order_by": "block_timestamp,asc"},
        )
        activation = None
        for row in rows:
            for contract in row.get("raw_data", {}).get("contract", []):
                value = contract["parameter"]["value"]
                if (
                    contract["type"] == "AccountCreateContract"
                    and tron_address(value.get("account_address", "")) == address
                ):
                    activation = tron_address(value["owner_address"])
                if (
                    contract["type"] == "TransferContract"
                    and tron_address(value["to_address"]) == address
                    and activation is None
                ):
                    activation = tron_address(value["owner_address"])
            if activation:
                break
        times = [
            datetime.fromtimestamp(r["raw_data"]["timestamp"] / 1000, timezone.utc) for r in rows
        ]
        profile = AddressProfile(
            chain="tron",
            address=address,
            first_seen=min(times) if times else None,
            last_seen=max(times) if times else None,
            tx_count=len(rows),
            activated_by=activation,
            is_contract=account.get("type") in {"Contract", 2},
            source=source,
            evidence=[
                {
                    "signal": "observed_activation_or_first_funding",
                    "note": "First observed sender is a heuristic when account creation is outside provider history",
                }
            ],
        )
        return store_profile(self.db, profile)

    async def get_balance(self, address, token=None):
        address = address_info("tron", address)[0]
        if token:
            token = address_info("tron", token)[0]
            data, source = await self.http.request(
                "/wallet/triggerconstantcontract",
                method="POST",
                body={
                    "owner_address": address,
                    "contract_address": token,
                    "function_selector": "balanceOf(address)",
                    "parameter": tron_hex(address)[2:].rjust(64, "0"),
                    "visible": True,
                },
            )
            amount = str(int(data["constant_result"][0], 16))
            symbol, decimals = await self.token_info(token, address)
        else:
            data, source = await self.http.request(
                "/wallet/getaccount", method="POST", body={"address": address, "visible": True}
            )
            amount, symbol, decimals = str(data.get("balance", 0)), "TRX", 6
        return {
            "chain": "tron",
            "address": address,
            "amount": amount,
            "token": symbol,
            "decimals": decimals,
            "source": source,
            "evidence": [{"signal": "provider_balance"}],
        }
