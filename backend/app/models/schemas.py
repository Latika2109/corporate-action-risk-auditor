"""Pydantic data contracts for the Corporate Action Risk Auditor."""
from __future__ import annotations

import uuid
from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def new_id() -> str:
    return str(uuid.uuid4())


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"


class ActionType(str, Enum):
    SPLIT = "SPLIT"
    BONUS = "BONUS"
    DIVIDEND = "DIVIDEND"
    MERGER = "MERGER"
    RIGHTS = "RIGHTS"
    UNKNOWN = "UNKNOWN"


class EventStatus(str, Enum):
    PENDING_VALIDATION = "PENDING_VALIDATION"
    VERIFIED = "VERIFIED"
    ANOMALY_DATE_MISMATCH = "ANOMALY_DATE_MISMATCH"
    ANOMALY_RATIO_MISMATCH = "ANOMALY_RATIO_MISMATCH"
    QUARANTINED = "QUARANTINED"


class ResolutionStatus(str, Enum):
    REQUIRES_HUMAN_REVIEW = "REQUIRES_HUMAN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalStatus(str, Enum):
    PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


# ---------------------------------------------------------------------------
# Canonical Corporate Action Event
# ---------------------------------------------------------------------------
class StandardizedEvent(BaseModel):
    event_id: str = Field(default_factory=new_id)
    isin: str
    exchange: Exchange
    action_type: ActionType
    ratio_multiplier: float
    ex_date: date
    record_date: date
    status: EventStatus = EventStatus.PENDING_VALIDATION
    raw_source_row: Optional[str] = None

    @field_validator("isin")
    @classmethod
    def isin_must_be_valid(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 12:
            raise ValueError(f"ISIN must be 12 chars, got '{v}' ({len(v)})")
        return v

    @field_validator("ratio_multiplier")
    @classmethod
    def ratio_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"ratio_multiplier must be positive, got {v}")
        return v


class QuarantinedRecord(BaseModel):
    quarantine_id: str = Field(default_factory=new_id)
    source_file: str
    exchange: Optional[Exchange] = None
    raw_row: str
    reason: str
    ingested_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Audit Anomaly Record
# ---------------------------------------------------------------------------
class ConflictType(str, Enum):
    DATE_MISMATCH = "DATE_MISMATCH"
    RATIO_MISMATCH = "RATIO_MISMATCH"


class AnomalyRecord(BaseModel):
    anomaly_id: str = Field(default_factory=new_id)
    isin: str
    conflict_type: ConflictType
    description: str
    nse_event_id: Optional[str] = None
    bse_event_id: Optional[str] = None
    resolution_status: ResolutionStatus = ResolutionStatus.REQUIRES_HUMAN_REVIEW


# ---------------------------------------------------------------------------
# Broker Holdings (mock ledger)
# ---------------------------------------------------------------------------
class BrokerHolding(BaseModel):
    holding_id: str = Field(default_factory=new_id)
    client_id: str
    isin: str
    shares: float
    unit_value: Optional[float] = None


# ---------------------------------------------------------------------------
# Shadow Simulation Entry
# ---------------------------------------------------------------------------
class ShadowSimulationEntry(BaseModel):
    simulation_id: str = Field(default_factory=new_id)
    client_id: str
    isin: str
    event_id: str
    pre_event_shares: float
    simulated_post_event_shares: float
    depository_expected_shares: Optional[float] = None
    variance: float = 0.0
    approval_status: ApprovalStatus = ApprovalStatus.PENDING_AUTHORIZATION


class SignOffRequest(BaseModel):
    simulation_id: str
    approver: str
    decision: ApprovalStatus


class IngestResponse(BaseModel):
    verified_events: list[StandardizedEvent]
    anomalies: list[AnomalyRecord]
    quarantined: list[QuarantinedRecord]
    total_rows_seen: int
