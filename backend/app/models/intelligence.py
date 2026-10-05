import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, BaseUnits, UTCDateTime, now


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    params_json: Mapped[dict] = mapped_column(JSON)
    seed: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class ScenarioTruth(Base):
    __tablename__ = "scenario_truth"
    scenario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("scenarios.id"), primary_key=True)
    deposit_address: Mapped[str] = mapped_column(String(100))
    hub_address: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100))
    per_case_amounts_json: Mapped[dict] = mapped_column(JSON)
    untraceable_amount: Mapped[str] = mapped_column(BaseUnits(), default="0")
    truth_json: Mapped[dict] = mapped_column(JSON)


class TransferRecord(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        UniqueConstraint("chain", "tx_hash", "log_index", name="uq_transfer_identity"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenarios.id"), index=True)
    chain: Mapped[str] = mapped_column(String(20), index=True)
    tx_hash: Mapped[str] = mapped_column(String(66), index=True)
    log_index: Mapped[int] = mapped_column(Integer)
    from_addr: Mapped[str] = mapped_column(String(100), index=True)
    to_addr: Mapped[str] = mapped_column(String(100), index=True)
    token: Mapped[str] = mapped_column(String(30))
    token_address: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[str] = mapped_column(BaseUnits())
    decimals: Mapped[int] = mapped_column(Integer)
    block_time: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    source: Mapped[str] = mapped_column(String(20))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AddressProfileRecord(Base):
    __tablename__ = "address_profiles"
    chain: Mapped[str] = mapped_column(String(20), primary_key=True)
    address: Mapped[str] = mapped_column(String(100), primary_key=True)
    scenario_key: Mapped[str] = mapped_column(String(40), primary_key=True, default="live")
    payload_json: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class ApiCache(Base):
    __tablename__ = "api_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    response_json: Mapped[dict] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    provider: Mapped[str] = mapped_column(String(60))


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    payload_json: Mapped[dict] = mapped_column(JSON)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CaseEvent(Base):
    __tablename__ = "case_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class TraceRun(Base):
    __tablename__ = "trace_runs"
    __table_args__ = (UniqueConstraint("case_id", "request_key", name="uq_trace_request"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    request_key: Mapped[str] = mapped_column(String(64))
    params_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    stop_summary_json: Mapped[dict] = mapped_column(JSON, default=dict)


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    type: Mapped[str] = mapped_column(String(30), default="exchange")
    country: Mapped[str] = mapped_column(String(3))
    vasp_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    source: Mapped[str] = mapped_column(String(20), default="public")


class Label(Base):
    __tablename__ = "labels"
    __table_args__ = (
        UniqueConstraint(
            "chain", "address", "entity_id", "source", "scenario_key", name="uq_label_source"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chain: Mapped[str] = mapped_column(String(20), index=True)
    address: Mapped[str] = mapped_column(String(100), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"))
    source: Mapped[str] = mapped_column(String(20))
    scenario_key: Mapped[str] = mapped_column(String(40), default="live")
    evidence_json: Mapped[list] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    decay_tau_days: Mapped[int] = mapped_column(Integer, default=180)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class GraphNode(Base):
    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("trace_run_id", "chain", "address", name="uq_graph_address"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trace_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trace_runs.id"), index=True)
    chain: Mapped[str] = mapped_column(String(20))
    address: Mapped[str] = mapped_column(String(100))
    hop: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(30), default="unknown")
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    label_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("labels.id"))
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("trace_run_id", "transfer_id", "to_node", name="uq_graph_transfer"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trace_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trace_runs.id"), index=True)
    from_node: Mapped[uuid.UUID] = mapped_column(ForeignKey("graph_nodes.id"))
    to_node: Mapped[uuid.UUID] = mapped_column(ForeignKey("graph_nodes.id"))
    transfer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transfers.id"))
    amount: Mapped[str] = mapped_column(BaseUnits())
    token: Mapped[str] = mapped_column(String(30))
    decimals: Mapped[int] = mapped_column(Integer)
    block_time: Mapped[datetime] = mapped_column(UTCDateTime())
    inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_json: Mapped[list] = mapped_column(JSON, default=list)


class PatternFinding(Base):
    __tablename__ = "pattern_findings"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    trace_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trace_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    node_ids_json: Mapped[list] = mapped_column(JSON)
    score: Mapped[float] = mapped_column(Float)
    evidence_json: Mapped[list] = mapped_column(JSON)


class Cluster(Base):
    __tablename__ = "clusters"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    chain: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("entities.id"))
    kind: Mapped[str] = mapped_column(String(40))
    created_from: Mapped[uuid.UUID] = mapped_column(ForeignKey("trace_runs.id"))


class ClusterMember(Base):
    __tablename__ = "cluster_members"
    cluster_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clusters.id"), primary_key=True)
    address: Mapped[str] = mapped_column(String(100), primary_key=True)
    evidence: Mapped[list] = mapped_column(JSON)


class Attribution(Base):
    __tablename__ = "attributions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    trace_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trace_runs.id"), unique=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("entities.id"))
    confidence: Mapped[float] = mapped_column(Float)
    hops: Mapped[int | None] = mapped_column(Integer)
    evidence_json: Mapped[list] = mapped_column(JSON)
    candidates_json: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="inferred")
    source: Mapped[str] = mapped_column(String(20))


class CalibrationMap(Base):
    __tablename__ = "calibration_maps"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    method: Mapped[str] = mapped_column(String(30))
    bins_json: Mapped[list] = mapped_column(JSON)
    fitted_on: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class ProbeRun(Base):
    __tablename__ = "probe_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"))
    chain: Mapped[str] = mapped_column(String(20))
    deposit_address: Mapped[str] = mapped_column(String(100))
    amount: Mapped[str] = mapped_column(BaseUnits())
    swept_to: Mapped[str] = mapped_column(String(100))
    sweep_delay_s: Mapped[int] = mapped_column(Integer)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    mode: Mapped[str] = mapped_column(String(20))


class HotwalletCluster(Base):
    __tablename__ = "hotwallet_clusters"
    __table_args__ = (UniqueConstraint("entity_id", "chain", "hub_address", name="uq_hotwallet"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"))
    chain: Mapped[str] = mapped_column(String(20))
    hub_address: Mapped[str] = mapped_column(String(100), index=True)
    matching_sweeps: Mapped[int] = mapped_column(Integer)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime())
    last_confirmed: Mapped[datetime] = mapped_column(UTCDateTime())
    source: Mapped[str] = mapped_column(String(20))


class Vasp(Base):
    __tablename__ = "vasps"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"))
    name: Mapped[str] = mapped_column(String(100), unique=True)
    country: Mapped[str] = mapped_column(String(3))
    registered_fiu: Mapped[bool] = mapped_column(Boolean)
    channel: Mapped[str] = mapped_column(String(40))
    endpoint: Mapped[str] = mapped_column(String(255))
    request_format: Mapped[dict] = mapped_column(JSON)
    avg_response_s: Mapped[float] = mapped_column(Float, default=0)
    response_rate: Mapped[float] = mapped_column(Float, default=0)
    responsiveness_ewma: Mapped[float] = mapped_column(Float, default=0.5)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[datetime | None] = mapped_column(UTCDateTime())
    source: Mapped[str] = mapped_column(String(20), default="simulated")


class FederatedQuery(Base):
    __tablename__ = "federated_queries"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    trace_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("trace_runs.id"))
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id"))
    query_hash: Mapped[str] = mapped_column(String(64))
    salt: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(20))
    targets_json: Mapped[list] = mapped_column(JSON)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class FederatedReply(Base):
    __tablename__ = "federated_replies"
    __table_args__ = (UniqueConstraint("query_id", "vasp_id", name="uq_federated_reply"),)
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    query_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("federated_queries.id"), index=True)
    vasp_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vasps.id"))
    match: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    response_s: Mapped[float | None] = mapped_column(Float)
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)


class RoutingDecision(Base):
    __tablename__ = "routing_decisions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id"), index=True)
    target_vasp_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vasps.id"))
    channel: Mapped[str] = mapped_column(String(40))
    issuer_target: Mapped[str | None] = mapped_column(String(40))
    reason_json: Mapped[list] = mapped_column(JSON)
    ranking_json: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
