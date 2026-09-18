"""Endpoints for Phase 1 + Phase 2: upload raw files, run normalization
and cross-exchange validation, persist results."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.ingestion import ingest_csv_bytes, ingest_text_dump
from app.core.validation import cross_validate
from app.models.db import AnomalyORM, EventORM, QuarantineORM, get_db
from app.models.schemas import Exchange, IngestResponse

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])


def _persist_results(db: Session, verified, anomalies, quarantined) -> None:
    for event in verified:
        db.merge(
            EventORM(
                event_id=event.event_id,
                isin=event.isin,
                exchange=event.exchange.value,
                action_type=event.action_type.value,
                ratio_multiplier=event.ratio_multiplier,
                ex_date=event.ex_date.isoformat(),
                record_date=event.record_date.isoformat(),
                status=event.status.value,
                raw_source_row=event.raw_source_row,
            )
        )
    for anomaly in anomalies:
        db.merge(
            AnomalyORM(
                anomaly_id=anomaly.anomaly_id,
                isin=anomaly.isin,
                conflict_type=anomaly.conflict_type.value,
                description=anomaly.description,
                nse_event_id=anomaly.nse_event_id,
                bse_event_id=anomaly.bse_event_id,
                resolution_status=anomaly.resolution_status.value,
            )
        )
    for record in quarantined:
        db.merge(
            QuarantineORM(
                quarantine_id=record.quarantine_id,
                source_file=record.source_file,
                exchange=record.exchange.value if record.exchange else None,
                raw_row=record.raw_row,
                reason=record.reason,
                ingested_at=record.ingested_at,
            )
        )
    db.commit()


@router.post("/run", response_model=IngestResponse)
async def run_ingestion_pipeline(
    nse_file: UploadFile = File(...),
    bse_file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> IngestResponse:
    """Upload one NSE file and one BSE file, run the full Phase 1 + 2
    pipeline, persist everything, and return the classified results.

    Accepts .csv (structured) or .txt (unstructured circular dump).
    """
    try:
        nse_bytes = await nse_file.read()
        bse_bytes = await bse_file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"could not read uploaded files: {exc}")

    def dispatch(filename: str, raw: bytes, exchange: Exchange):
        if filename.lower().endswith(".txt"):
            return ingest_text_dump(raw.decode("utf-8", errors="replace"), exchange, filename)
        return ingest_csv_bytes(raw, exchange, filename)

    nse_events, nse_quarantine = dispatch(nse_file.filename or "nse_upload", nse_bytes, Exchange.NSE)
    bse_events, bse_quarantine = dispatch(bse_file.filename or "bse_upload", bse_bytes, Exchange.BSE)

    verified, anomalies = cross_validate(nse_events, bse_events)
    quarantined = [*nse_quarantine, *bse_quarantine]

    _persist_results(db, verified, anomalies, quarantined)

    total_rows = len(nse_events) + len(bse_events) + len(quarantined)

    return IngestResponse(
        verified_events=verified,
        anomalies=anomalies,
        quarantined=quarantined,
        total_rows_seen=total_rows,
    )
