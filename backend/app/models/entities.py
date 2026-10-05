import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def now():
    return datetime.now(timezone.utc)


class Role(str, Enum):
    INVESTIGATOR = "INVESTIGATOR"
    SUPERVISOR = "SUPERVISOR"
    ADMIN = "ADMIN"


class CaseStatus(str, Enum):
    OPEN = "OPEN"
    TRACING = "TRACING"
    ATTRIBUTED = "ATTRIBUTED"
    FREEZE_PENDING = "FREEZE_PENDING"
    CLOSED = "CLOSED"


class Chain(str, Enum):
    ETHEREUM = "ethereum"
    BNB = "bnb"
    POLYGON = "polygon"
    TRON = "tron"
    BITCOIN = "bitcoin"


class Base(DeclarativeBase):
    pass


class BaseUnits(TypeDecorator):
    """Postgres NUMERIC(38,0); text on SQLite to avoid SQLite float coercion."""
    impl = Numeric(38, 0)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String(38) if dialect.name == "sqlite" else Numeric(38, 0))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return str(value) if dialect.name == "sqlite" else Decimal(value)

    def process_result_value(self, value, dialect):
        return str(value) if value is not None else None


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('INVESTIGATOR','SUPERVISOR','ADMIN')"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    pending_totp_secret: Mapped[str | None] = mapped_column(String(64))
    last_totp_step: Mapped[int | None] = mapped_column(Integer)
    session_version: Mapped[int] = mapped_column(Integer, default=0)
    org_unit: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (CheckConstraint("status IN ('OPEN','TRACING','ATTRIBUTED','FREEZE_PENDING','CLOSED')"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_ref: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    sahyog_ref: Mapped[str | None] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="OPEN", index=True)
    gang_case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    transactions: Mapped[list["VictimTransaction"]] = relationship(back_populates="case", cascade="all, delete-orphan", lazy="selectin", order_by="VictimTransaction.created_at")
    attachments: Mapped[list["CaseAttachment"]] = relationship(lazy="selectin")


class VictimTransaction(Base):
    __tablename__ = "victim_transactions"
    __table_args__ = (
        UniqueConstraint("case_id", "chain", "tx_hash", name="uq_case_chain_tx"),
        CheckConstraint("decimals IS NULL OR (decimals >= 0 AND decimals <= 30)"),
        CheckConstraint("chain IN ('ethereum','bnb','polygon','tron','bitcoin')"),
        CheckConstraint("status IN ('PENDING_RESOLUTION','REPORTED')"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    chain: Mapped[str] = mapped_column(String(20), index=True)
    tx_hash: Mapped[str | None] = mapped_column(String(66))
    victim_address: Mapped[str | None] = mapped_column(String(100))
    suspect_address: Mapped[str | None] = mapped_column(String(100))
    token: Mapped[str | None] = mapped_column(String(20))
    amount: Mapped[str | None] = mapped_column(BaseUnits())
    decimals: Mapped[int | None] = mapped_column(Integer)
    tx_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    case: Mapped[Case] = relationship(back_populates="transactions")


class CaseAttachment(Base):
    __tablename__ = "case_attachments"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"), index=True)
    file_path: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    extracted_json: Mapped[dict] = mapped_column(JSON)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
