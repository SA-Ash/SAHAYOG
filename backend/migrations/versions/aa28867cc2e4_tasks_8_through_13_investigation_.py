from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models.entities import BaseUnits

revision: str = "aa28867cc2e4"
down_revision: Union[str, None] = "02a821866746"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_head",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=100), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_hash"),
    )
    op.create_table(
        "forecast_models",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trained_on", sa.String(length=200), nullable=False),
        sa.Column("transition_matrix_json", sa.JSON(), nullable=False),
        sa.Column("delay_params_json", sa.JSON(), nullable=False),
        sa.Column("evaluation_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )
    op.create_table(
        "gang_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "case_collisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_a", sa.Uuid(), nullable=False),
        sa.Column("case_b", sa.Uuid(), nullable=False),
        sa.Column("hashed_cluster_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("room_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_a"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["case_b"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_a", "case_b", "hashed_cluster_id"),
    )
    op.create_table(
        "case_similarity",
        sa.Column("case_a", sa.Uuid(), nullable=False),
        sa.Column("case_b", sa.Uuid(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_a"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["case_b"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("case_a", "case_b"),
    )
    op.create_table(
        "forecasts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["forecast_models.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_forecasts_case_id"), "forecasts", ["case_id"], unique=False)
    op.create_table(
        "freeze_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("target_vasp_id", sa.Uuid(), nullable=True),
        sa.Column("target_address", sa.String(length=100), nullable=False),
        sa.Column("target_chain", sa.String(length=20), nullable=False),
        sa.Column("amount_by_case_json", sa.JSON(), nullable=False),
        sa.Column("issuer_target", sa.String(length=30), nullable=True),
        sa.Column("impact_level", sa.String(length=12), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("proposer_id", sa.Uuid(), nullable=True),
        sa.Column("approver_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("golden_hour", sa.Boolean(), nullable=False),
        sa.Column("second_key_due", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sahyog_request_id", sa.String(length=100), nullable=True),
        sa.Column("issuer_request_id", sa.String(length=100), nullable=True),
        sa.Column("package_json", sa.JSON(), nullable=False),
        sa.Column("dispatch_error", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["approver_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["proposer_id"],
            ["users.id"],
        ),
        sa.ForeignKeyConstraint(
            ["target_vasp_id"],
            ["vasps.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_freeze_requests_case_id"), "freeze_requests", ["case_id"], unique=False
    )
    op.create_table(
        "gang_case_members",
        sa.Column("gang_case_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["gang_case_id"],
            ["gang_cases.id"],
        ),
        sa.PrimaryKeyConstraint("gang_case_id", "case_id"),
    )
    op.create_table(
        "gang_exit_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("gang_case_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", BaseUnits(precision=38, scale=0), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["gang_case_id"],
            ["gang_cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "entity_id"),
    )
    op.create_table(
        "operator_fingerprints",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("vector_json", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("bundle_hash", sa.String(length=64), nullable=False),
        sa.Column("merkle_root", sa.String(length=64), nullable=False),
        sa.Column("pdf_path", sa.String(length=500), nullable=False),
        sa.Column("pdf_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bundle_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_hash"),
        sa.UniqueConstraint("case_id", "version"),
    )
    op.create_table(
        "wallet_farms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("funder_address", sa.String(length=100), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("first_seen_window", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "funder_address"),
    )
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("leaf_hash", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_evidence_items_report_id"), "evidence_items", ["report_id"], unique=False
    )
    op.create_table(
        "evidence_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["report_id"],
            ["reports.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_evidence_snapshots_report_id"), "evidence_snapshots", ["report_id"], unique=False
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["freeze_requests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notifications_user_id"), "notifications", ["user_id"], unique=False)
    op.create_table(
        "request_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("from_state", sa.String(length=20), nullable=False),
        sa.Column("to_state", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(length=2000), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["freeze_requests.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_request_events_request_id"), "request_events", ["request_id"], unique=False
    )
    op.create_table(
        "taint_lots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("origin_tx", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("origin_amount", BaseUnits(precision=38, scale=0), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["origin_tx"],
            ["victim_transactions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("origin_tx"),
    )
    op.create_table(
        "taint_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("trace_run_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(length=12), nullable=False),
        sa.Column("cutoff", sa.String(length=20), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["trace_run_id"],
            ["trace_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_taint_runs_case_id"), "taint_runs", ["case_id"], unique=False)
    op.create_table(
        "blast_radius_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("taint_run_id", sa.Uuid(), nullable=False),
        sa.Column("target_chain", sa.String(length=20), nullable=False),
        sa.Column("target_address", sa.String(length=100), nullable=False),
        sa.Column("traceable_amount", BaseUnits(precision=38, scale=0), nullable=False),
        sa.Column("est_balance", BaseUnits(precision=38, scale=0), nullable=False),
        sa.Column("impact_level", sa.String(length=12), nullable=False),
        sa.Column("swept", sa.Boolean(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["taint_run_id"],
            ["taint_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_blast_radius_reports_case_id"), "blast_radius_reports", ["case_id"], unique=False
    )
    op.create_table(
        "taint_balances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("chain", sa.String(length=20), nullable=False),
        sa.Column("address", sa.String(length=100), nullable=False),
        sa.Column("token", sa.String(length=30), nullable=False),
        sa.Column("token_address", sa.String(length=100), nullable=True),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("amount", BaseUnits(precision=38, scale=0), nullable=False),
        sa.Column("method", sa.String(length=12), nullable=False),
        sa.Column("as_of_time", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["taint_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_taint_balances_address"), "taint_balances", ["address"], unique=False)
    op.create_index(op.f("ix_taint_balances_run_id"), "taint_balances", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_taint_balances_run_id"), table_name="taint_balances")
    op.drop_index(op.f("ix_taint_balances_address"), table_name="taint_balances")
    op.drop_table("taint_balances")
    op.drop_index(op.f("ix_blast_radius_reports_case_id"), table_name="blast_radius_reports")
    op.drop_table("blast_radius_reports")
    op.drop_index(op.f("ix_taint_runs_case_id"), table_name="taint_runs")
    op.drop_table("taint_runs")
    op.drop_table("taint_lots")
    op.drop_index(op.f("ix_request_events_request_id"), table_name="request_events")
    op.drop_table("request_events")
    op.drop_index(op.f("ix_notifications_user_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_evidence_snapshots_report_id"), table_name="evidence_snapshots")
    op.drop_table("evidence_snapshots")
    op.drop_index(op.f("ix_evidence_items_report_id"), table_name="evidence_items")
    op.drop_table("evidence_items")
    op.drop_table("wallet_farms")
    op.drop_table("reports")
    op.drop_table("operator_fingerprints")
    op.drop_table("gang_exit_history")
    op.drop_table("gang_case_members")
    op.drop_index(op.f("ix_freeze_requests_case_id"), table_name="freeze_requests")
    op.drop_table("freeze_requests")
    op.drop_index(op.f("ix_forecasts_case_id"), table_name="forecasts")
    op.drop_table("forecasts")
    op.drop_table("case_similarity")
    op.drop_table("case_collisions")
    op.drop_table("gang_cases")
    op.drop_table("forecast_models")
    op.drop_table("audit_log")
    op.drop_table("audit_head")
