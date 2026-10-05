import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.intelligence import AddressProfileRecord, TransferRecord
from app.schemas.intelligence import Transfer


def transfer_of(row):
    return Transfer(
        chain=row.chain,
        tx_hash=row.tx_hash,
        log_index=row.log_index,
        from_addr=row.from_addr,
        to_addr=row.to_addr,
        token=row.token,
        token_address=row.token_address,
        amount=row.amount,
        decimals=row.decimals,
        block_time=row.block_time,
        source=row.source,
        metadata=row.metadata_json,
        evidence=[
            {
                "signal": "chain_transfer",
                "source": row.source,
                "tx_hash": row.tx_hash,
                "log_index": row.log_index,
            }
        ],
    )


def store_transfers(db, transfers, scenario_id=None, commit=True):
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    for transfer in transfers:
        values = transfer.model_dump(exclude={"evidence", "metadata"}, mode="python")
        values["chain"] = transfer.chain.value
        values.update(id=uuid.uuid4(), scenario_id=scenario_id, metadata_json=transfer.metadata)
        db.execute(
            insert(TransferRecord)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["chain", "tx_hash", "log_index"])
        )
    if commit:
        db.commit()
    return [
        db.scalar(
            select(TransferRecord).where(
                TransferRecord.chain == t.chain,
                TransferRecord.tx_hash == t.tx_hash,
                TransferRecord.log_index == t.log_index,
            )
        )
        for t in transfers
    ]


def store_profile(db, profile, scenario_key="live", commit=True):
    key = (profile.chain.value, profile.address, scenario_key)
    row = db.get(AddressProfileRecord, key)
    if not row:
        row = AddressProfileRecord(
            chain=key[0], address=key[1], scenario_key=key[2], payload_json={}
        )
        db.add(row)
    row.payload_json = profile.model_dump(mode="json")
    if commit:
        db.commit()
    return profile
