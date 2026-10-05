from sqlalchemy import or_, select

from app.adapters.base import ChainAdapter
from app.adapters.storage import transfer_of
from app.core.errors import AppError
from app.models.intelligence import AddressProfileRecord, TransferRecord
from app.schemas.intelligence import AddressProfile
from app.services.validation import address_info, normalize_hash


class SyntheticAdapter(ChainAdapter):
    def __init__(self, db, chain, scenario_id):
        self.db, self.chain, self.scenario_id = db, chain, scenario_id

    def query(self):
        return select(TransferRecord).where(
            TransferRecord.scenario_id == self.scenario_id, TransferRecord.chain == self.chain
        )

    async def get_tx(self, tx_hash):
        rows = self.db.scalars(
            self.query()
            .where(TransferRecord.tx_hash == normalize_hash(self.chain, tx_hash))
            .order_by(TransferRecord.log_index)
        ).all()
        if not rows:
            raise AppError("CHAIN_RECORD_NOT_FOUND", "Synthetic transaction not found", 404)
        return [transfer_of(row) for row in rows]

    async def get_transfers(self, address, direction="out", time_from=None, time_to=None):
        address = address_info(self.chain, address)[0]
        query = self.query()
        if direction == "out":
            query = query.where(TransferRecord.from_addr == address)
        elif direction == "in":
            query = query.where(TransferRecord.to_addr == address)
        else:
            query = query.where(
                or_(TransferRecord.from_addr == address, TransferRecord.to_addr == address)
            )
        if time_from:
            query = query.where(TransferRecord.block_time >= time_from)
        if time_to:
            query = query.where(TransferRecord.block_time <= time_to)
        return [
            transfer_of(row)
            for row in self.db.scalars(
                query.order_by(TransferRecord.block_time, TransferRecord.log_index)
            ).all()
        ]

    async def get_profile(self, address):
        address = address_info(self.chain, address)[0]
        row = self.db.get(AddressProfileRecord, (self.chain, address, str(self.scenario_id)))
        if not row:
            return AddressProfile(
                chain=self.chain, address=address, source="synthetic", is_contract=False
            )
        return AddressProfile.model_validate(row.payload_json)

    async def get_balance(self, address, token=None):
        transfers = await self.get_transfers(address, "both")
        totals = {}
        for t in transfers:
            if token and t.token != token:
                continue
            key = (t.token, t.decimals)
            totals[key] = totals.get(key, 0) + (
                int(t.amount) if t.to_addr == address else -int(t.amount)
            )
        return {
            "chain": self.chain,
            "address": address,
            "balances": [
                {"token": t, "decimals": d, "amount": str(v)} for (t, d), v in totals.items()
            ],
            "source": "synthetic",
            "evidence": [
                {
                    "signal": "synthetic_net_flow",
                    "note": "Initial balances are outside the generated history",
                }
            ],
        }
