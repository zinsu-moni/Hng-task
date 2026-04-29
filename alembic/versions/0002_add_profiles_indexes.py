from alembic import op

revision = "0002_add_profiles_indexes"
down_revision = "0001_create_profiles_table"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("idx_profiles_gender", "profiles", ["gender"], unique=False)
    op.create_index("idx_profiles_age_group", "profiles", ["age_group"], unique=False)
    op.create_index("idx_profiles_country_id", "profiles", ["country_id"], unique=False)
    op.create_index("idx_profiles_age", "profiles", ["age"], unique=False)
    op.create_index("idx_profiles_gender_probability", "profiles", ["gender_probability"], unique=False)
    op.create_index("idx_profiles_country_probability", "profiles", ["country_probability"], unique=False)
    op.create_index("idx_profiles_created_at", "profiles", ["created_at"], unique=False)


def downgrade():
    op.drop_index("idx_profiles_created_at", table_name="profiles")
    op.drop_index("idx_profiles_country_probability", table_name="profiles")
    op.drop_index("idx_profiles_gender_probability", table_name="profiles")
    op.drop_index("idx_profiles_age", table_name="profiles")
    op.drop_index("idx_profiles_country_id", table_name="profiles")
    op.drop_index("idx_profiles_age_group", table_name="profiles")
    op.drop_index("idx_profiles_gender", table_name="profiles")

