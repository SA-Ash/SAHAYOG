from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.models.entities import BaseUnits

revision: str = "02a821866746"
down_revision: Union[str, None] = "433d5b173cce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "address_profiles",
        sa.Column("chain", sa.String(length=20), nullable=False),
        sa.Column("address", sa.String(length=100), nullable=False),
        sa.Column("scenario_key", sa.String(length=40), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("chain", "address", "scenario_key"),
    )
    op.create_table(
        "api_cache",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=60), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "calibration_maps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("bins_json", sa.JSON(), nullable=False),
        sa.Column("fitted_on", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("country", sa.String(length=3), nullable=False),
        sa.Column("vasp_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "scenarios",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "hotwallet_clusters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("chain", sa.String(length=20), nullable=False),
        sa.Column("hub_address", sa.String(length=100), nullable=False),
        sa.Column("matching_sweeps", sa.Integer(), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_confirmed", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_id", "chain", "hub_address", name="uq_hotwallet"),
    )
    op.create_index(
        op.f("ix_hotwallet_clusters_hub_address"),
        "hotwallet_clusters",
        ["hub_address"],
        unique=False,
    )
    op.create_table(
        "labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chain", sa.String(length=20), nullable=False),
        sa.Column("address", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("scenario_key", sa.String(length=40), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decay_tau_days", sa.Integer(), nullable=False),
        sa.Column("verified_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chain", "address", "entity_id", "source", "scenario_key", name="uq_label_source"
        ),
    )
    op.create_index(op.f("ix_labels_address"), "labels", ["address"], unique=False)
    op.create_index(op.f("ix_labels_chain"), "labels", ["chain"], unique=False)
    op.create_table(
        "probe_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("chain", sa.String(length=20), nullable=False),
        sa.Column("deposit_address", sa.String(length=100), nullable=False),
        sa.Column("amount", BaseUnits(), nullable=False),
        sa.Column("swept_to", sa.String(length=100), nullable=False),
        sa.Column("sweep_delay_s", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "scenario_truth",
        sa.Column("scenario_id", sa.Uuid(), nullable=False),
        sa.Column("deposit_address", sa.String(length=100), nullable=False),
        sa.Column("hub_address", sa.String(length=100), nullable=False),
        sa.Column("entity", sa.String(length=100), nullable=False),
        sa.Column("per_case_amounts_json", sa.JSON(), nullable=False),
        sa.Column("untraceable_amount", BaseUnits(), nullable=False),
        sa.Column("truth_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
        ),
        sa.PrimaryKeyConstraint("scenario_id"),
    )
    op.create_table(
        "transfers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scenario_id", sa.Uuid(), nullable=True),
        sa.Column("chain", sa.String(length=20), nullable=False),
        sa.Column("tx_hash", sa.String(length=66), nullable=False),
        sa.Column("log_index", sa.Integer(), nullable=False),
        sa.Column("from_addr", sa.String(length=100), nullable=False),
        sa.Column("to_addr", sa.String(length=100), nullable=False),
        sa.Column("token", sa.String(length=30), nullable=False),
        sa.Column("token_address", sa.String(length=100), nullable=True),
        sa.Column("amount", BaseUnits(), nullable=False),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("block_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["scenarios.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chain", "tx_hash", "log_index", name="uq_transfer_identity"),
    )
    op.create_index(op.f("ix_transfers_block_time"), "transfers", ["block_time"], unique=False)
    op.create_index(op.f("ix_transfers_chain"), "transfers", ["chain"], unique=False)
    op.create_index(op.f("ix_transfers_from_addr"), "transfers", ["from_addr"], unique=False)
    op.create_index(op.f("ix_transfers_scenario_id"), "transfers", ["scenario_id"], unique=False)
    op.create_index(op.f("ix_transfers_to_addr"), "transfers", ["to_addr"], unique=False)
    op.create_index(op.f("ix_transfers_tx_hash"), "transfers", ["tx_hash"], unique=False)
    op.create_table(
        "vasps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("country", sa.String(length=3), nullable=False),
        sa.Column("registered_fiu", sa.Boolean(), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("request_format", sa.JSON(), nullable=False),
        sa.Column("avg_response_s", sa.Float(), nullable=False),
        sa.Column("response_rate", sa.Float(), nullable=False),
        sa.Column("responsiveness_ewma", sa.Float(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("successes", sa.Integer(), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "case_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_case_events_case_id"), "case_events", ["case_id"], unique=False)
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_case_id"), "jobs", ["case_id"], unique=False)
    op.create_table(
        "routing_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("target_vasp_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("issuer_target", sa.String(length=40), nullable=True),
        sa.Column("reason_json", sa.JSON(), nullable=False),
        sa.Column("ranking_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["target_vasp_id"],
            ["vasps.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_routing_decisions_case_id"), "routing_decisions", ["case_id"], unique=False
    )
    op.create_table(
        "trace_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_summary_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "request_key", name="uq_trace_request"),
    )
    op.create_index(op.f("ix_trace_runs_case_id"), "trace_runs", ["case_id"], unique=False)
    op.create_table(
        "attributions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("trace_run_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("hops", sa.Integer(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("candidates_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
        ),
        sa.ForeignKeyConstraint(
            ["trace_run_id"],
            ["trace_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trace_run_id"),
    )
    op.create_index(op.f("ix_attributions_case_id"), "attributions", ["case_id"], unique=False)
    op.create_table(
        "clusters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chain", sa.String(length=20), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("created_from", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_from"],
            ["trace_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "federated_queries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("trace_run_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("salt", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("targets_json", sa.JSON(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.id"],
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["trace_run_id"],
            ["trace_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_federated_queries_case_id"), "federated_queries", ["case_id"], unique=False
    )
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_run_id", sa.Uuid(), nullable=False),
        sa.Column("chain", sa.String(length=20), nullable=False),
        sa.Column("address", sa.String(length=100), nullable=False),
        sa.Column("hop", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=True),
        sa.Column("label_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["label_id"],
            ["labels.id"],
        ),
        sa.ForeignKeyConstraint(
            ["trace_run_id"],
            ["trace_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trace_run_id", "chain", "address", name="uq_graph_address"),
    )
    op.create_index(
        op.f("ix_graph_nodes_trace_run_id"), "graph_nodes", ["trace_run_id"], unique=False
    )
    op.create_table(
        "pattern_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_run_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("node_ids_json", sa.JSON(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["trace_run_id"],
            ["trace_runs.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pattern_findings_trace_run_id"), "pattern_findings", ["trace_run_id"], unique=False
    )
    op.create_table(
        "cluster_members",
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.String(length=100), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cluster_id"],
            ["clusters.id"],
        ),
        sa.PrimaryKeyConstraint("cluster_id", "address"),
    )
    op.create_table(
        "federated_replies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("query_id", sa.Uuid(), nullable=False),
        sa.Column("vasp_id", sa.Uuid(), nullable=False),
        sa.Column("match", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_s", sa.Float(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["query_id"],
            ["federated_queries.id"],
        ),
        sa.ForeignKeyConstraint(
            ["vasp_id"],
            ["vasps.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("query_id", "vasp_id", name="uq_federated_reply"),
    )
    op.create_index(
        op.f("ix_federated_replies_query_id"), "federated_replies", ["query_id"], unique=False
    )
    op.create_table(
        "graph_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("trace_run_id", sa.Uuid(), nullable=False),
        sa.Column("from_node", sa.Uuid(), nullable=False),
        sa.Column("to_node", sa.Uuid(), nullable=False),
        sa.Column("transfer_id", sa.Uuid(), nullable=False),
        sa.Column("amount", BaseUnits(), nullable=False),
        sa.Column("token", sa.String(length=30), nullable=False),
        sa.Column("decimals", sa.Integer(), nullable=False),
        sa.Column("block_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inferred", sa.Boolean(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["from_node"],
            ["graph_nodes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["to_node"],
            ["graph_nodes.id"],
        ),
        sa.ForeignKeyConstraint(
            ["trace_run_id"],
            ["trace_runs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["transfers.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trace_run_id", "transfer_id", "to_node", name="uq_graph_transfer"),
    )
    op.create_index(
        op.f("ix_graph_edges_trace_run_id"), "graph_edges", ["trace_run_id"], unique=False
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE cases ADD COLUMN scenario_id CHAR(32) REFERENCES scenarios(id)")
    else:
        op.add_column("cases", sa.Column("scenario_id", sa.Uuid(), nullable=True))
        op.create_foreign_key("fk_cases_scenario", "cases", "scenarios", ["scenario_id"], ["id"])


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("ALTER TABLE cases DROP COLUMN scenario_id")
    else:
        op.drop_constraint("fk_cases_scenario", "cases", type_="foreignkey")
        op.drop_column("cases", "scenario_id")
    op.drop_index(op.f("ix_graph_edges_trace_run_id"), table_name="graph_edges")
    op.drop_table("graph_edges")
    op.drop_index(op.f("ix_federated_replies_query_id"), table_name="federated_replies")
    op.drop_table("federated_replies")
    op.drop_table("cluster_members")
    op.drop_index(op.f("ix_pattern_findings_trace_run_id"), table_name="pattern_findings")
    op.drop_table("pattern_findings")
    op.drop_index(op.f("ix_graph_nodes_trace_run_id"), table_name="graph_nodes")
    op.drop_table("graph_nodes")
    op.drop_index(op.f("ix_federated_queries_case_id"), table_name="federated_queries")
    op.drop_table("federated_queries")
    op.drop_table("clusters")
    op.drop_index(op.f("ix_attributions_case_id"), table_name="attributions")
    op.drop_table("attributions")
    op.drop_index(op.f("ix_trace_runs_case_id"), table_name="trace_runs")
    op.drop_table("trace_runs")
    op.drop_index(op.f("ix_routing_decisions_case_id"), table_name="routing_decisions")
    op.drop_table("routing_decisions")
    op.drop_index(op.f("ix_jobs_case_id"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_index(op.f("ix_case_events_case_id"), table_name="case_events")
    op.drop_table("case_events")
    op.drop_table("vasps")
    op.drop_index(op.f("ix_transfers_tx_hash"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_to_addr"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_scenario_id"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_from_addr"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_chain"), table_name="transfers")
    op.drop_index(op.f("ix_transfers_block_time"), table_name="transfers")
    op.drop_table("transfers")
    op.drop_table("scenario_truth")
    op.drop_table("probe_runs")
    op.drop_index(op.f("ix_labels_chain"), table_name="labels")
    op.drop_index(op.f("ix_labels_address"), table_name="labels")
    op.drop_table("labels")
    op.drop_index(op.f("ix_hotwallet_clusters_hub_address"), table_name="hotwallet_clusters")
    op.drop_table("hotwallet_clusters")
    op.drop_table("settings")
    op.drop_table("scenarios")
    op.drop_table("entities")
    op.drop_table("calibration_maps")
    op.drop_table("api_cache")
    op.drop_table("address_profiles")
