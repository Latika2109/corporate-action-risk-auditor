"""SQLAlchemy models + engine setup.

Uses SQLite for local dev; swap DATABASE_URL env var to a postgres:// DSN
for production (Render/Railway inject this automatically).
"""
from __future__ import annotations

import os

from sqlalchemy import (
    Column,
    Float,
    String,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./risk_auditor.db")
if DATABASE_URL.startswith("postgres://"):
    # SQLAlchemy 2.x requires the "postgresql://" scheme; Render/Railway
    # still hand out the legacy "postgres://" form.
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class EventORM(Base):
    __tablename__ = "corporate_action_events"

    event_id = Column(String, primary_key=True)
    isin = Column(String, index=True, nullable=False)
    exchange = Column(String, nullable=False)
    action_type = Column(String, nullable=False)
    ratio_multiplier = Column(Float, nullable=False)
    ex_date = Column(String, nullable=False)
    record_date = Column(String, nullable=False)
    status = Column(String, nullable=False)
    raw_source_row = Column(String, nullable=True)


class QuarantineORM(Base):
    __tablename__ = "quarantine_queue"

    quarantine_id = Column(String, primary_key=True)
    source_file = Column(String, nullable=False)
    exchange = Column(String, nullable=True)
    raw_row = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    ingested_at = Column(String, nullable=True)


class AnomalyORM(Base):
    __tablename__ = "anomalies"

    anomaly_id = Column(String, primary_key=True)
    isin = Column(String, index=True, nullable=False)
    conflict_type = Column(String, nullable=False)
    description = Column(String, nullable=False)
    nse_event_id = Column(String, nullable=True)
    bse_event_id = Column(String, nullable=True)
    resolution_status = Column(String, nullable=False)


class BrokerHoldingORM(Base):
    __tablename__ = "broker_holdings"

    holding_id = Column(String, primary_key=True)
    client_id = Column(String, index=True, nullable=False)
    isin = Column(String, index=True, nullable=False)
    shares = Column(Float, nullable=False)
    unit_value = Column(Float, nullable=True)


class ShadowSimulationORM(Base):
    __tablename__ = "shadow_simulations"

    simulation_id = Column(String, primary_key=True)
    client_id = Column(String, index=True, nullable=False)
    isin = Column(String, index=True, nullable=False)
    event_id = Column(String, nullable=False)
    pre_event_shares = Column(Float, nullable=False)
    simulated_post_event_shares = Column(Float, nullable=False)
    depository_expected_shares = Column(Float, nullable=True)
    variance = Column(Float, default=0.0)
    approval_status = Column(String, nullable=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
