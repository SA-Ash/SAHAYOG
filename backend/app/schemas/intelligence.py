import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.entities import Chain
from app.schemas.cases import BaseUnitString
from app.services.validation import address_info, normalize_hash


class Transfer(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    chain: Chain
    tx_hash: str
    log_index: int = Field(ge=-1000000)
    from_addr: str
    to_addr: str
    token: str = Field(min_length=1, max_length=30)
    token_address: str | None = None
    amount: BaseUnitString
    decimals: int = Field(ge=0, le=30)
    block_time: datetime
    source: Literal["live", "cache", "synthetic"]
    metadata: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid(self):
        self.tx_hash = normalize_hash(self.chain, self.tx_hash)
        self.from_addr = address_info(self.chain, self.from_addr)[0]
        self.to_addr = address_info(self.chain, self.to_addr)[0]
        if self.token_address:
            self.token_address = address_info(self.chain, self.token_address)[0]
        if self.block_time.utcoffset() is None:
            raise ValueError("Block timestamp requires timezone")
        self.amount = str(int(self.amount))
        return self


class AddressProfile(BaseModel):
    chain: Chain
    address: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    tx_count: int = 0
    activated_by: str | None = None
    gas_funder: str | None = None
    is_contract: bool | None = None
    source: str
    evidence: list[dict] = Field(default_factory=list)


class ScenarioInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["easy", "hard", "multi-victim"] = "hard"
    seed: int = Field(default=42, ge=0, le=2147483647)
    chain: Chain = Chain.TRON
    victims: int = Field(default=1, ge=1, le=20)
    hops: int = Field(default=4, ge=1, le=12)
    noise_level: int = Field(default=20, ge=0, le=300)
    split_merge_intensity: int = Field(default=0, ge=0, le=5)
    with_bridge: bool = False
    with_swap: bool = False
    mixer: bool = False
    peel_chain_len: int = Field(default=0, ge=0, le=12)
    farm_size: int = Field(default=0, ge=0, le=30)
    mixer_denoms: list[BaseUnitString] = Field(default_factory=lambda: ["1000000000"], max_length=5)
    dormant_days: int = Field(default=0, ge=0, le=365)
    operator_profile: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def supported(self):
        if (self.with_bridge or self.with_swap) and self.chain not in {
            Chain.ETHEREUM,
            Chain.POLYGON,
        }:
            raise ValueError("Bridge/swap presets use Ethereum or Polygon")
        if self.with_bridge and self.with_swap:
            raise ValueError("Choose a bridge or swap fixture per scenario")
        if self.mixer and (not self.mixer_denoms or any(int(a) == 0 for a in self.mixer_denoms)):
            raise ValueError("Mixer denominations must be positive")
        if self.preset == "multi-victim":
            self.victims = 5
        return self


class TraceParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_hops: int = Field(default=12, ge=1, le=30)
    min_amount: BaseUnitString = "1"
    max_fanout: int = Field(default=20, ge=1, le=100)
    max_nodes: int = Field(default=500, ge=2, le=3000)
    start_chain: Chain | None = None
    start_address: str | None = None
    trigger_id: uuid.UUID | None = None
    high_volume_degree: int = Field(default=200, ge=20, le=5000)
    time_from: datetime | None = None
    time_to: datetime | None = None

    @model_validator(mode="after")
    def dates(self):
        if bool(self.start_chain) != bool(self.start_address):
            raise ValueError("Retrace requires chain and address together")
        if self.start_address:
            self.start_address = address_info(self.start_chain, self.start_address)[0]
        for value in [self.time_from, self.time_to]:
            if value and value.utcoffset() is None:
                raise ValueError("Time windows require timezone")
        if self.time_from and self.time_to and self.time_from > self.time_to:
            raise ValueError("Invalid time window")
        return self


class LabelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain: Chain
    address: str
    entity_id: uuid.UUID
    source: Literal["public", "inferred"] = "public"
    evidence: list[dict] = Field(min_length=1, max_length=20)
    confidence: float = Field(default=0.7, ge=0, le=0.9)
    last_confirmed_at: datetime | None = None
    decay_tau_days: int = Field(default=180, ge=1, le=3650)

    @model_validator(mode="after")
    def valid(self):
        self.address = address_info(self.chain, self.address)[0]
        if self.last_confirmed_at and self.last_confirmed_at.utcoffset() is None:
            raise ValueError("Confirmation timestamp requires timezone")
        return self


class ProbeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: uuid.UUID
    chain: Chain = Chain.TRON
    count: int = Field(default=10, ge=1, le=100)
    seed: int = Field(default=42, ge=0, le=2147483647)


class LookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["hash", "psi"] = "hash"


class ReplayInput(BaseModel):
    enabled: bool


class SwapServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    chain: Chain
    address: str
    input_token: str = Field(min_length=1, max_length=30)
    input_contract: str | None = None
    destination_chain: Chain
    output_address: str
    output_token: str = Field(min_length=1, max_length=30)
    output_contract: str | None = None
    rate: Decimal = Field(gt=0, le=1000000000)
    fee: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    tolerance: Decimal = Field(default=Decimal(".02"), gt=0, le=Decimal(".1"))
    window_seconds: int = Field(default=300, ge=1, le=3600)
    reference: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def addresses(self):
        self.address = address_info(self.chain, self.address)[0]
        self.output_address = address_info(self.destination_chain, self.output_address)[0]
        if self.input_contract:
            self.input_contract = address_info(self.chain, self.input_contract)[0]
        if self.output_contract:
            self.output_contract = address_info(self.destination_chain, self.output_contract)[0]
        return self
