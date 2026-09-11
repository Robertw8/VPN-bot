"""Phase 8: observed server configuration, health and reconciliation diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "b826ac07d881"
down_revision = "af730acb7790"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("servers", sa.Column("connection_config", sa.JSON(), nullable=True))
    op.add_column(
        "servers", sa.Column("health", sa.String(16), nullable=False, server_default="UNKNOWN")
    )
    op.add_column(
        "servers", sa.Column("health_checked_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_servers_health_state"),
        "servers",
        "health IN ('UNKNOWN', 'HEALTHY', 'DEGRADED', 'OFFLINE')",
    )
    op.add_column("vpn_configs", sa.Column("reconcile_issue", sa.String(40), nullable=True))
    op.add_column(
        "vpn_configs", sa.Column("remote_checked_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade():
    op.drop_column("vpn_configs", "remote_checked_at")
    op.drop_column("vpn_configs", "reconcile_issue")
    op.drop_constraint(op.f("ck_servers_health_state"), "servers", type_="check")
    op.drop_column("servers", "health_checked_at")
    op.drop_column("servers", "health")
    op.drop_column("servers", "connection_config")
