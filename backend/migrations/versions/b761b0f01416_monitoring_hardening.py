from alembic import op

revision = "b761b0f01416"
down_revision = "aa28867cc2e4"
branch_labels = None
depends_on = None


def upgrade():
    for name in (
        "fence_members",
        "fence_proposals",
        "monitor_settings",
        "dormancy_state",
        "alerts",
        "benchmark_runs",
    ):
        from app.models.entities import Base

        Base.metadata.tables[name].create(op.get_bind())


def downgrade():
    for name in (
        "benchmark_runs",
        "alerts",
        "dormancy_state",
        "monitor_settings",
        "fence_proposals",
        "fence_members",
    ):
        op.drop_table(name)
