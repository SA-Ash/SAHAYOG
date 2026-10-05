import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.entities import Chain
from app.services.validation import address_info, normalize_hash

BaseUnitString = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9]{1,38}$")]


class TransactionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain: Chain
    tx_hash: str | None = Field(default=None, max_length=66)
    victim_address: str | None = Field(default=None, max_length=100)
    suspect_address: str | None = Field(default=None, max_length=100)
    token: str | None = Field(default=None, pattern=r"^[A-Z0-9._-]{1,20}$")
    amount: BaseUnitString | None = None
    decimals: int | None = Field(default=None, ge=0, le=30, strict=True)
    tx_time: datetime | None = None

    @model_validator(mode="after")
    def validate_transaction(self):
        if not self.tx_hash and not self.suspect_address:
            raise ValueError("Provide a transaction hash or suspect address")
        if self.tx_hash:
            self.tx_hash = normalize_hash(self.chain, self.tx_hash)
        for name in ("victim_address", "suspect_address"):
            if value := getattr(self, name):
                setattr(self, name, address_info(self.chain, value)[0])
        if self.amount is not None:
            if int(self.amount) <= 0:
                raise ValueError("Reported amount must be positive")
            self.amount = str(int(self.amount))
            if self.token is None or self.decimals is None:
                raise ValueError("Amount requires a token and its decimal precision")
        if self.tx_time and self.tx_time.utcoffset() is None:
            raise ValueError("Transaction timestamp must include a timezone")
        return self


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="Blockchain complaint", min_length=3, max_length=160)
    sahyog_ref: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_/-]{3,80}$")
    transaction: TransactionInput
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)
    extraction_reviewed: bool = False


class Complaint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(pattern=r"^[A-Za-z0-9_/-]{3,80}$")
    title: str = Field(min_length=3, max_length=160)
    transaction: TransactionInput
    source: Literal["simulated", "live"] = "simulated"


class TransactionOut(TransactionInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sha256: str
    extracted_json: dict
    created_at: datetime


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    case_ref: str
    sahyog_ref: str | None
    title: str
    status: str
    gang_case_id: uuid.UUID | None
    created_by: uuid.UUID
    created_at: datetime
    source: str
    transactions: list[TransactionOut]
    attachments: list[AttachmentOut]


class CasePage(BaseModel):
    items: list[CaseOut]
    total: int
    page: int
    page_size: int
