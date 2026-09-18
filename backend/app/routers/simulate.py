"""Endpoints for Phase 3 + Phase 4: shadow simulation and human sign-off."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.simulation import apply_sign_off, simulate_holdings_for_event
from app.models.db import BrokerHoldingORM, EventORM, ShadowSimulationORM, get_db
from app.models.schemas import (
    ApprovalStatus,
    BrokerHolding,
    EventStatus,
    ShadowSimulationEntry,
    SignOffRequest,
    StandardizedEvent,
)

router = APIRouter(prefix="/api/simulate", tags=["simulation"])


@router.post("/event/{event_id}", response_model=list[ShadowSimulationEntry])
def simulate_event(event_id: str, db: Session = Depends(get_db)) -> list[ShadowSimulationEntry]:
    """Run the shadow simulation for one VERIFIED event against current
    mock broker holdings. Refuses to simulate unverified/anomalous events."""
    event_row = db.query(EventORM).filter(EventORM.event_id == event_id).first()
    if not event_row:
        raise HTTPException(status_code=404, detail="event not found")
    if event_row.status != EventStatus.VERIFIED.value:
        raise HTTPException(
            status_code=409,
            detail=f"event status is '{event_row.status}', must be VERIFIED to simulate",
        )

    event = StandardizedEvent(
        event_id=event_row.event_id,
        isin=event_row.isin,
        exchange=event_row.exchange,
        action_type=event_row.action_type,
        ratio_multiplier=event_row.ratio_multiplier,
        ex_date=event_row.ex_date,
        record_date=event_row.record_date,
        status=event_row.status,
    )

    holding_rows = db.query(BrokerHoldingORM).filter(BrokerHoldingORM.isin == event.isin).all()
    holdings = [
        BrokerHolding(
            holding_id=h.holding_id,
            client_id=h.client_id,
            isin=h.isin,
            shares=h.shares,
            unit_value=h.unit_value,
        )
        for h in holding_rows
    ]

    entries = simulate_holdings_for_event(event, holdings)

    for entry in entries:
        db.merge(
            ShadowSimulationORM(
                simulation_id=entry.simulation_id,
                client_id=entry.client_id,
                isin=entry.isin,
                event_id=entry.event_id,
                pre_event_shares=entry.pre_event_shares,
                simulated_post_event_shares=entry.simulated_post_event_shares,
                depository_expected_shares=entry.depository_expected_shares,
                variance=entry.variance,
                approval_status=entry.approval_status.value,
            )
        )
    db.commit()

    return entries


@router.get("/pending", response_model=list[ShadowSimulationEntry])
def list_pending_simulations(db: Session = Depends(get_db)) -> list[ShadowSimulationEntry]:
    rows = (
        db.query(ShadowSimulationORM)
        .filter(ShadowSimulationORM.approval_status == ApprovalStatus.PENDING_AUTHORIZATION.value)
        .all()
    )
    return [
        ShadowSimulationEntry(
            simulation_id=r.simulation_id,
            client_id=r.client_id,
            isin=r.isin,
            event_id=r.event_id,
            pre_event_shares=r.pre_event_shares,
            simulated_post_event_shares=r.simulated_post_event_shares,
            depository_expected_shares=r.depository_expected_shares,
            variance=r.variance,
            approval_status=r.approval_status,
        )
        for r in rows
    ]


@router.post("/signoff", response_model=ShadowSimulationEntry)
def sign_off(request: SignOffRequest, db: Session = Depends(get_db)) -> ShadowSimulationEntry:
    """Human-in-the-loop authorization: moves a simulation entry from
    PENDING_AUTHORIZATION to APPROVED or REJECTED."""
    row = (
        db.query(ShadowSimulationORM)
        .filter(ShadowSimulationORM.simulation_id == request.simulation_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="simulation entry not found")

    entry = ShadowSimulationEntry(
        simulation_id=row.simulation_id,
        client_id=row.client_id,
        isin=row.isin,
        event_id=row.event_id,
        pre_event_shares=row.pre_event_shares,
        simulated_post_event_shares=row.simulated_post_event_shares,
        depository_expected_shares=row.depository_expected_shares,
        variance=row.variance,
        approval_status=row.approval_status,
    )
    entry = apply_sign_off(entry, request.decision)

    row.approval_status = entry.approval_status.value
    db.commit()

    return entry
