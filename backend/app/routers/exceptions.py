"""Read endpoints backing the Streamlit Exception Dashboard."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.models.db import AnomalyORM, BrokerHoldingORM, EventORM, QuarantineORM, get_db
from app.models.schemas import AnomalyRecord, BrokerHolding, QuarantinedRecord, StandardizedEvent

router = APIRouter(prefix="/api", tags=["exceptions"])


@router.get("/events", response_model=list[StandardizedEvent])
def list_events(db: Session = Depends(get_db)) -> list[StandardizedEvent]:
    rows = db.query(EventORM).all()
    return [
        StandardizedEvent(
            event_id=r.event_id,
            isin=r.isin,
            exchange=r.exchange,
            action_type=r.action_type,
            ratio_multiplier=r.ratio_multiplier,
            ex_date=r.ex_date,
            record_date=r.record_date,
            status=r.status,
            raw_source_row=r.raw_source_row,
        )
        for r in rows
    ]


@router.get("/anomalies", response_model=list[AnomalyRecord])
def list_anomalies(db: Session = Depends(get_db)) -> list[AnomalyRecord]:
    rows = db.query(AnomalyORM).all()
    return [
        AnomalyRecord(
            anomaly_id=r.anomaly_id,
            isin=r.isin,
            conflict_type=r.conflict_type,
            description=r.description,
            nse_event_id=r.nse_event_id,
            bse_event_id=r.bse_event_id,
            resolution_status=r.resolution_status,
        )
        for r in rows
    ]


@router.get("/quarantine", response_model=list[QuarantinedRecord])
def list_quarantine(db: Session = Depends(get_db)) -> list[QuarantinedRecord]:
    rows = db.query(QuarantineORM).all()
    return [
        QuarantinedRecord(
            quarantine_id=r.quarantine_id,
            source_file=r.source_file,
            exchange=r.exchange,
            raw_row=r.raw_row,
            reason=r.reason,
            ingested_at=r.ingested_at,
        )
        for r in rows
    ]


@router.get("/holdings", response_model=list[BrokerHolding])
def list_holdings(db: Session = Depends(get_db)) -> list[BrokerHolding]:
    rows = db.query(BrokerHoldingORM).all()
    return [
        BrokerHolding(
            holding_id=r.holding_id,
            client_id=r.client_id,
            isin=r.isin,
            shares=r.shares,
            unit_value=r.unit_value,
        )
        for r in rows
    ]
