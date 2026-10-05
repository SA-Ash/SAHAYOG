import uuid
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, UTCDateTime, now


class FenceMember(Base):
    __tablename__ = "fence_members"
    __table_args__ = (UniqueConstraint("case_id", "chain", "address"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    chain: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(100))
    added_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    cursor_json: Mapped[dict] = mapped_column(JSON, default=dict)


class FenceProposal(Base):
    __tablename__ = "fence_proposals"
    __table_args__ = (UniqueConstraint("case_id", "chain", "address", "direction"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    chain: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(100))
    direction: Mapped[str] = mapped_column(String(12))
    reason_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")


class MonitorSettings(Base):
    __tablename__ = "monitor_settings"
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    threshold_days: Mapped[int] = mapped_column(Integer, default=30)
    poll_seconds: Mapped[int] = mapped_column(Integer, default=60)
    last_polled: Mapped[datetime | None] = mapped_column(UTCDateTime())
    victims_json: Mapped[list] = mapped_column(JSON, default=list)


class DormancyState(Base):
    __tablename__ = "dormancy_state"
    __table_args__ = (UniqueConstraint("case_id", "chain", "address"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    chain: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(100))
    last_active: Mapped[datetime | None] = mapped_column(UTCDateTime())
    dormant_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    threshold_days: Mapped[int] = mapped_column(Integer, default=30)
    tainted_balance_json: Mapped[dict] = mapped_column(JSON, default=dict)
    cursor_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("case_id", "event_key"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    event_key: Mapped[str] = mapped_column(String(250))
    payload_json: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    ack_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    dataset: Mapped[str] = mapped_column(String(150))
    result_json: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
