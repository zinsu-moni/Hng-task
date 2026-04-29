import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.utils.uuid7 import uuid7


class Profile(Base):
    __tablename__ = "profiles"

    # UUIDv7 primary key; generated in the app/seed (DB default is intentionally not set).
    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid7,
        nullable=False,
    )

    # Unique person identifier (unique constraint on DB side).
    name: Mapped[str] = mapped_column(sa.String(), nullable=False, unique=True)

    gender: Mapped[str] = mapped_column(sa.String(), nullable=False)  # 'male' | 'female'
    gender_probability: Mapped[float] = mapped_column(sa.Float(), nullable=False)

    age: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    age_group: Mapped[str] = mapped_column(sa.String(), nullable=False)  # child|teenager|adult|senior

    country_id: Mapped[str] = mapped_column(sa.String(2), nullable=False)  # ISO 3166-1 alpha-2
    country_name: Mapped[str] = mapped_column(sa.String(), nullable=False)
    country_probability: Mapped[float] = mapped_column(sa.Float(), nullable=False)

    # Auto-generated (UTC).
    created_at: Mapped[dt.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )

    __table_args__ = (
        sa.Index("idx_profiles_gender", "gender"),
        sa.Index("idx_profiles_age_group", "age_group"),
        sa.Index("idx_profiles_country_id", "country_id"),
        sa.Index("idx_profiles_age", "age"),
        sa.Index("idx_profiles_gender_probability", "gender_probability"),
        sa.Index("idx_profiles_country_probability", "country_probability"),
        sa.Index("idx_profiles_created_at", "created_at"),
    )

