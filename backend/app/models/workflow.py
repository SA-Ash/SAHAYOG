import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint, Uuid, event
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.entities import Base, BaseUnits, UTCDateTime, now


class TaintLot(Base):
    __tablename__ = "taint_lots"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    origin_tx: Mapped[uuid.UUID] = mapped_column(ForeignKey("victim_transactions.id"), unique=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"))
    origin_amount: Mapped[str] = mapped_column(BaseUnits())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class TaintRun(Base):
    __tablename__ = "taint_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    trace_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trace_runs.id"))
    method: Mapped[str] = mapped_column(String(12))
    cutoff: Mapped[str] = mapped_column(String(20))
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class TaintBalance(Base):
    __tablename__ = "taint_balances"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("taint_runs.id"), index=True)
    chain: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(100), index=True)
    token: Mapped[str] = mapped_column(String(30))
    token_address: Mapped[str | None] = mapped_column(String(100))
    decimals: Mapped[int] = mapped_column(Integer)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id"))
    amount: Mapped[str] = mapped_column(BaseUnits())
    method: Mapped[str] = mapped_column(String(12))
    as_of_time: Mapped[datetime] = mapped_column(UTCDateTime())


class GangCase(Base):
    __tablename__ = "gang_cases"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160))
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class GangCaseMember(Base):
    __tablename__ = "gang_case_members"
    gang_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gang_cases.id"), primary_key=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), primary_key=True)


class WalletFarm(Base):
    __tablename__ = "wallet_farms"
    __table_args__ = (UniqueConstraint("case_id", "funder_address"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"))
    funder_address: Mapped[str] = mapped_column(String(100))
    member_count: Mapped[int] = mapped_column(Integer)
    first_seen_window: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="suggested")
    evidence_json: Mapped[dict] = mapped_column(JSON)


class OperatorFingerprint(Base):
    __tablename__ = "operator_fingerprints"
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    vector_json: Mapped[dict] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class CaseSimilarity(Base):
    __tablename__ = "case_similarity"
    case_a: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    case_b: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), primary_key=True)
    result_json: Mapped[dict] = mapped_column(JSON)


class CaseCollision(Base):
    __tablename__ = "case_collisions"
    __table_args__ = (UniqueConstraint("case_a", "case_b", "hashed_cluster_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_a: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"))
    case_b: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"))
    hashed_cluster_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="new")
    room_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class BlastRadiusReport(Base):
    __tablename__ = "blast_radius_reports"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    taint_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("taint_runs.id"))
    target_chain: Mapped[str] = mapped_column(String(20))
    target_address: Mapped[str] = mapped_column(String(100))
    traceable_amount: Mapped[str] = mapped_column(BaseUnits())
    est_balance: Mapped[str] = mapped_column(BaseUnits())
    impact_level: Mapped[str] = mapped_column(String(12))
    swept: Mapped[bool] = mapped_column(Boolean)
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class FreezeRequest(Base):
    __tablename__ = "freeze_requests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    target_vasp_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vasps.id"))
    target_address: Mapped[str] = mapped_column(String(100))
    target_chain: Mapped[str] = mapped_column(String(20))
    amount_by_case_json: Mapped[dict] = mapped_column(JSON)
    issuer_target: Mapped[str | None] = mapped_column(String(30))
    impact_level: Mapped[str] = mapped_column(String(12))
    state: Mapped[str] = mapped_column(String(20), default="DRAFT")
    proposer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approver_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(2000), default="")
    golden_hour: Mapped[bool] = mapped_column(Boolean, default=False)
    second_key_due: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sahyog_request_id: Mapped[str | None] = mapped_column(String(100))
    issuer_request_id: Mapped[str | None] = mapped_column(String(100))
    package_json: Mapped[dict] = mapped_column(JSON)
    dispatch_error: Mapped[dict | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=0)
    __mapper_args__ = {"version_id_col": version}
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class RequestEvent(Base):
    __tablename__ = "request_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("freeze_requests.id"), index=True)
    from_state: Mapped[str] = mapped_column(String(20))
    to_state: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    note: Mapped[str] = mapped_column(String(2000))
    at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class AuditHead(Base):
    __tablename__ = "audit_head"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    entry_hash: Mapped[str] = mapped_column(String(64), default="0" * 64)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str | None] = mapped_column(String(100))
    payload_json: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime())


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("freeze_requests.id"))
    kind: Mapped[str] = mapped_column(String(50))
    payload_json: Mapped[dict] = mapped_column(JSON)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("case_id", "version"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"))
    version: Mapped[int] = mapped_column(Integer)
    bundle_hash: Mapped[str] = mapped_column(String(64), unique=True)
    merkle_root: Mapped[str] = mapped_column(String(64))
    pdf_path: Mapped[str] = mapped_column(String(500))
    pdf_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    bundle_json: Mapped[dict] = mapped_column(JSON)


class EvidenceSnapshot(Base):
    __tablename__ = "evidence_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reports.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    payload_json: Mapped[dict] = mapped_column(JSON)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("reports.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    leaf_hash: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    payload_json: Mapped[dict] = mapped_column(JSON)


class ForecastModel(Base):
    __tablename__ = "forecast_models"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    trained_on: Mapped[str] = mapped_column(String(200))
    transition_matrix_json: Mapped[dict] = mapped_column(JSON)
    delay_params_json: Mapped[dict] = mapped_column(JSON)
    evaluation_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Forecast(Base):
    __tablename__ = "forecasts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forecast_models.id"))
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class GangExitHistory(Base):
    __tablename__ = "gang_exit_history"
    __table_args__ = (UniqueConstraint("case_id", "entity_id"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    gang_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gang_cases.id"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"))
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"))
    exit_time: Mapped[datetime] = mapped_column(UTCDateTime())
    amount: Mapped[str] = mapped_column(BaseUnits())


@event.listens_for(Session, "before_flush")
def immutable_evidence(session, *_):
    for row in session.dirty | session.deleted:
        if isinstance(row, (AuditLog, Report, EvidenceSnapshot, EvidenceItem, RequestEvent)) and (
            row in session.deleted or session.is_modified(row)
        ):
            raise ValueError("Append-only evidence cannot be changed")
