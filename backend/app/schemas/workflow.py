from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.entities import Chain
from app.schemas.cases import BaseUnitString
from app.services.validation import address_info


class TaintInput(BaseModel):
    method: Literal["haircut", "fifo", "poison"] = "haircut"
    cutoff: Decimal = Field(default=Decimal(".02"), ge=0, le=1)


class TargetInput(BaseModel):
    chain: Chain | None = None
    address: str | None = None

    @model_validator(mode="after")
    def valid(self):
        if bool(self.chain) != bool(self.address):
            raise ValueError("Supply both target chain and address")
        if self.address:
            self.address = address_info(self.chain, self.address)[0]
        return self


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["FREEZE", "DISCLOSURE", "BOTH"] = "BOTH"
    amount: BaseUnitString | None = None
    expiry_minutes: int = Field(default=1440, ge=5, le=43200)
    mock_outcome: Literal["frozen", "declined", "delayed"] = "delayed"


class ReasonInput(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)


class GoldenInput(ReasonInput):
    minutes: int = Field(default=30, ge=1, le=60)


class ForecastInput(BaseModel):
    rollouts: int = Field(default=1000, ge=100, le=10000)
    seed: int = Field(default=42, ge=0, le=2147483647)


class SplitInput(BaseModel):
    available_amount: BaseUnitString


class ReportInput(BaseModel):
    title: str = Field(default="Investigation evidence package", min_length=1, max_length=160)
