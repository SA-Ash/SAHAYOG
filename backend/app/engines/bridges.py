from datetime import timedelta

from eth_abi import decode
from eth_utils import keccak

from app.core.errors import AppError
from app.engines.signals import match_swap
from app.models.intelligence import Setting
from app.schemas.intelligence import SwapServiceInput

CCTP_TOPIC = (
    "0x"
    + keccak(
        text="DepositForBurn(uint64,address,uint256,address,bytes32,uint32,bytes32,bytes32)"
    ).hex()
)
SWAP_TOPIC = "0x" + keccak(text="Swap(address,uint256,uint256,uint256,uint256,address)").hex()
CCTP_CONTRACTS = {
    "ethereum": "0xbd3fa81b58ba92a82136038b25adec7066af3155",
    "polygon": "0x9daf8c91aefae50b9c0e69629d3f6ca40ca3b3fe",
}
DOMAINS = {0: "ethereum", 7: "polygon"}
MESSAGE_TOPIC = "0x" + keccak(text="MessageReceived(address,uint32,uint64,bytes32,bytes)").hex()
MINT_TOPIC = "0x" + keccak(text="MintAndWithdraw(address,uint256,address)").hex()
USDC = {
    "ethereum": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
    "polygon": "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359",
}
UNISWAP_ROUTER = "0x7a250d5630b4cf539739df2c5dacab4c659f2488d"


class BridgeDecoder:
    @staticmethod
    def decode_cctp(chain, log):
        topics = log.get("topics", [])
        if (
            log.get("address", "").lower() != CCTP_CONTRACTS.get(chain)
            or len(topics) != 4
            or topics[0].lower() != CCTP_TOPIC
        ):
            return None
        amount, recipient, domain, messenger, caller = decode(
            ["uint256", "bytes32", "uint32", "bytes32", "bytes32"],
            bytes.fromhex(log["data"].removeprefix("0x")),
        )
        if domain not in DOMAINS:
            raise AppError(
                "UNSUPPORTED_BRIDGE",
                "CCTP destination chain is unsupported",
                422,
                {"domain": domain},
            )
        return {
            "protocol": "cctp_v1",
            "destination_chain": DOMAINS[domain],
            "recipient": "0x" + recipient.hex()[-40:],
            "amount": str(amount),
            "decimals": 6,
            "token": "USDC",
            "nonce": int(topics[1], 16),
            "source_domain": next(d for d, name in DOMAINS.items() if name == chain),
            "destination_messenger": "0x" + messenger.hex()[-40:],
            "burn_token": "0x" + topics[2][-40:],
            "depositor": "0x" + topics[3][-40:],
            "evidence": [
                {
                    "signal": "cctp_burn_event",
                    "destination_domain": domain,
                    "note": "Source event proves intended recipient; destination mint must be independently observed",
                }
            ],
        }

    @classmethod
    async def confirm_cctp(cls, bridge, transfer, registry):
        chain = bridge["destination_chain"]
        if not registry or bridge["destination_messenger"] != CCTP_CONTRACTS[chain]:
            raise AppError(
                "UNSUPPORTED_BRIDGE", "CCTP destination requires independent mint confirmation", 422
            )
        adapter = registry.get(chain)
        transmitter, _ = await adapter.call(
            "proxy",
            "eth_call",
            to=CCTP_CONTRACTS[chain],
            data="0x" + keccak(text="localMessageTransmitter()")[:4].hex(),
            tag="latest",
        )
        transmitter = "0x" + transmitter[-40:].lower()
        candidates = await adapter.get_transfers(
            bridge["recipient"], "in", transfer.block_time, transfer.block_time + timedelta(days=1)
        )
        for output in candidates:
            if (
                output.token_address != USDC[chain]
                or output.amount != bridge["amount"]
                or output.from_addr != "0x" + "0" * 40
            ):
                continue
            logs = output.metadata.get("receipt_logs", [])
            minted = any(
                log.get("address", "").lower() == CCTP_CONTRACTS[chain]
                and len(log.get("topics", [])) == 3
                and log["topics"][0].lower() == MINT_TOPIC
                and "0x" + log["topics"][1][-40:].lower() == bridge["recipient"]
                and "0x" + log["topics"][2][-40:].lower() == USDC[chain]
                and int(log["data"], 16) == int(bridge["amount"])
                for log in logs
            )
            if not minted:
                continue
            for log in logs:
                topics = log.get("topics", [])
                if (
                    log.get("address", "").lower() != transmitter
                    or len(topics) != 3
                    or topics[0].lower() != MESSAGE_TOPIC
                    or int(topics[2], 16) != bridge["nonce"]
                ):
                    continue
                domain, sender, body = decode(
                    ["uint32", "bytes32", "bytes"], bytes.fromhex(log["data"].removeprefix("0x"))
                )
                if (
                    domain == bridge["source_domain"]
                    and "0x" + sender.hex()[-40:] == CCTP_CONTRACTS[transfer.chain.value]
                    and len(body) == 132
                    and int.from_bytes(body[:4]) == 0
                    and "0x" + body[4:36].hex()[-40:] == bridge["burn_token"]
                    and "0x" + body[36:68].hex()[-40:] == bridge["recipient"]
                    and int.from_bytes(body[68:100]) == int(bridge["amount"])
                    and "0x" + body[100:132].hex()[-40:] == bridge["depositor"]
                ):
                    return {
                        **bridge,
                        "token_address": output.token_address,
                        "block_time": output.block_time,
                        "kind": "bridge",
                        "inferred": False,
                        "evidence": [
                            *bridge["evidence"],
                            {
                                "signal": "cctp_destination_mint_and_message",
                                "tx_hash": output.tx_hash,
                                "nonce": bridge["nonce"],
                                "source_domain": domain,
                            },
                        ],
                    }
        raise AppError(
            "UNSUPPORTED_BRIDGE",
            "CCTP burn observed; destination mint not confirmed in the 24-hour search window",
            422,
        )

    @staticmethod
    async def infer_swap(transfer, registry):
        if not registry or registry.scenario_id:
            return None
        setting = registry.db.get(Setting, "swap_services")
        for raw in setting.value.get("services", []) if setting else []:
            service = SwapServiceInput.model_validate(raw)
            if (
                service.chain != transfer.chain
                or service.address != transfer.to_addr
                or service.input_token != transfer.token
                or service.input_contract != transfer.token_address
            ):
                continue
            end = transfer.block_time + timedelta(seconds=service.window_seconds)
            outputs = await registry.get(service.destination_chain).get_transfers(
                service.output_address, "out", transfer.block_time, end
            )
            outputs = [
                t
                for t in outputs
                if t.token == service.output_token and t.token_address == service.output_contract
            ]
            matched = match_swap(
                transfer.amount,
                transfer.decimals,
                transfer.block_time,
                outputs,
                service.rate,
                str(service.fee),
                service.window_seconds,
                str(service.tolerance),
            )
            if not matched:
                raise AppError(
                    "UNSUPPORTED_BRIDGE",
                    "Swap outputs are missing or ambiguous; manual follow-up required",
                    422,
                )
            output = matched["transfer"]
            return {
                "kind": "swap",
                "protocol": service.name,
                "destination_chain": output.chain.value,
                "recipient": output.to_addr,
                "amount": output.amount,
                "token": output.token,
                "token_address": output.token_address,
                "decimals": output.decimals,
                "block_time": output.block_time,
                "inferred": True,
                "evidence": [
                    *matched["evidence"],
                    {
                        "signal": "configured_swap_service",
                        "reference": service.reference,
                        "output_tx_hash": output.tx_hash,
                        "confidence": matched["confidence"],
                    },
                ],
            }
        return None

    @classmethod
    async def resolve(cls, transfer, adapter, registry=None):
        if transfer.source == "synthetic":
            for kind in ["bridge", "swap"]:
                if kind in transfer.metadata:
                    return {
                        **transfer.metadata[kind],
                        "kind": kind,
                        "inferred": False,
                        "evidence": [{"signal": kind + "_fixture", "source": "simulated"}],
                    }
        for log in transfer.metadata.get("receipt_logs", []):
            bridge = cls.decode_cctp(transfer.chain.value, log)
            if (
                bridge
                and bridge["depositor"] == transfer.from_addr
                and bridge["burn_token"] == transfer.token_address
                and bridge["amount"] == transfer.amount
            ):
                return await cls.confirm_cctp(bridge, transfer, registry)
        if (
            transfer.chain.value == "ethereum"
            and transfer.metadata.get("transaction_to", "").lower() == UNISWAP_ROUTER
        ):
            outputs = await adapter.get_tx(transfer.tx_hash)
            candidates = [
                t
                for t in outputs
                if t.token_address != transfer.token_address
                and t.to_addr == transfer.from_addr
                and t.log_index > transfer.log_index
            ]
            swaps = [
                log
                for log in transfer.metadata.get("receipt_logs", [])
                if log.get("topics", [None])[0] == SWAP_TOPIC
            ]
            if swaps and len(candidates) == 1:
                output = candidates[0]
                return {
                    "kind": "swap",
                    "protocol": "uniswap_v2",
                    "destination_chain": output.chain.value,
                    "recipient": output.to_addr,
                    "token": output.token,
                    "token_address": output.token_address,
                    "amount": output.amount,
                    "decimals": output.decimals,
                    "inferred": False,
                    "evidence": [
                        {
                            "signal": "uniswap_v2_swap_and_transfer_receipt",
                            "output_log_index": output.log_index,
                        }
                    ],
                }
        inferred_swap = await cls.infer_swap(transfer, registry)
        if inferred_swap:
            return inferred_swap
        if transfer.to_addr in set(CCTP_CONTRACTS.values()) or transfer.metadata.get(
            "unsupported_bridge"
        ):
            raise AppError("UNSUPPORTED_BRIDGE", "Bridge requires manual follow-up", 422)
        return None
