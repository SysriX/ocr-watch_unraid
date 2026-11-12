"""Erste Tabellen: einstellungen, profile, jobs, dateien

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "einstellungen",
        sa.Column("schluessel", sa.String(64), primary_key=True),
        sa.Column("wert", sa.JSON(), nullable=True),
    )
    op.create_table(
        "profile",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("aktiv", sa.Boolean(), nullable=False),
        sa.Column("pfad", sa.String(1024), nullable=False),
        sa.Column("optionen", sa.JSON(), nullable=False),
        sa.Column("erstellt", sa.DateTime(), nullable=False),
        sa.Column("geaendert", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profil_id", sa.Integer(),
                  sa.ForeignKey("profile.id", ondelete="SET NULL"), nullable=True),
        sa.Column("profil_name", sa.String(100), nullable=False),
        sa.Column("art", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("quellen", sa.JSON(), nullable=False),
        sa.Column("fehler_pfade", sa.JSON(), nullable=False),
        sa.Column("ziel", sa.String(1024), nullable=False),
        sa.Column("meldung", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("erstellt", sa.DateTime(), nullable=False),
        sa.Column("gestartet", sa.DateTime(), nullable=True),
        sa.Column("beendet", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_jobs_profil_id", "jobs", ["profil_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_erstellt", "jobs", ["erstellt"])
    op.create_table(
        "dateien",
        sa.Column("pfad_hash", sa.String(64), primary_key=True),
        sa.Column("pfad", sa.String(1024), nullable=False),
        sa.Column("groesse", sa.BigInteger(), nullable=False),
        sa.Column("mtime", sa.BigInteger(), nullable=False),
        sa.Column("profil_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=True),
        sa.Column("gesehen", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_dateien_profil_id", "dateien", ["profil_id"])


def downgrade() -> None:
    op.drop_table("dateien")
    op.drop_table("jobs")
    op.drop_table("profile")
    op.drop_table("einstellungen")
