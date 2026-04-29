from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_create_profiles_table"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("gender", sa.String(), nullable=False),
        sa.Column("gender_probability", sa.Float(), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("age_group", sa.String(), nullable=False),
        sa.Column("country_id", sa.String(length=2), nullable=False),
        sa.Column("country_name", sa.String(), nullable=False),
        sa.Column("country_probability", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("name"),
    )


def downgrade():
    op.drop_table("profiles")

