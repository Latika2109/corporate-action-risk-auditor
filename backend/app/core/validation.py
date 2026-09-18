"""Phase 2: Cross-Exchange Validation Engine.

Joins NSE and BSE normalized events on ISIN and applies the rule matrix:
  Rule 1 - Date Match:  nse.record_date vs bse.record_date
  Rule 2 - Ratio Match: nse.ratio_multiplier vs bse.ratio_multiplier

Returns verified events (auto-matched, no conflicts) separately from
anomalies requiring human review.
"""
from __future__ import annotations

import pandas as pd

from app.models.schemas import (
    AnomalyRecord,
    ConflictType,
    EventStatus,
    StandardizedEvent,
)

RATIO_TOLERANCE = 1e-4


def _events_to_df(events: list[StandardizedEvent]) -> pd.DataFrame:
    if not events:
        return pd.DataFrame(
            columns=["event_id", "isin", "ratio_multiplier", "ex_date", "record_date"]
        )
    return pd.DataFrame(
        [
            {
                "event_id": e.event_id,
                "isin": e.isin,
                "ratio_multiplier": e.ratio_multiplier,
                "ex_date": e.ex_date,
                "record_date": e.record_date,
            }
            for e in events
        ]
    )


def cross_validate(
    nse_events: list[StandardizedEvent],
    bse_events: list[StandardizedEvent],
) -> tuple[list[StandardizedEvent], list[AnomalyRecord]]:
    """Run the ISIN join + date/ratio rule matrix.

    Returns (verified_events, anomalies). Events present on only one
    exchange pass through as verified (nothing to cross-check yet) but
    remain PENDING_VALIDATION so downstream simulation can still act on
    single-source confirmations if desired.
    """
    nse_by_id = {e.event_id: e for e in nse_events}
    bse_by_id = {e.event_id: e for e in bse_events}

    nse_df = _events_to_df(nse_events)
    bse_df = _events_to_df(bse_events)

    verified: list[StandardizedEvent] = []
    anomalies: list[AnomalyRecord] = []

    if nse_df.empty or bse_df.empty:
        # Nothing to cross-check; pass everything through untouched.
        for e in [*nse_events, *bse_events]:
            verified.append(e)
        return verified, anomalies

    merged = nse_df.merge(
        bse_df,
        on="isin",
        how="outer",
        suffixes=("_nse", "_bse"),
        indicator=True,
    )

    matched_isins: set[str] = set()

    for _, row in merged[merged["_merge"] == "both"].iterrows():
        isin = row["isin"]
        matched_isins.add(isin)
        nse_event = nse_by_id[row["event_id_nse"]]
        bse_event = bse_by_id[row["event_id_bse"]]

        row_anomalies: list[AnomalyRecord] = []

        # Rule 1: Date Match
        if row["record_date_nse"] != row["record_date_bse"]:
            row_anomalies.append(
                AnomalyRecord(
                    isin=isin,
                    conflict_type=ConflictType.DATE_MISMATCH,
                    description=(
                        f"NSE record date {row['record_date_nse']} conflicts with "
                        f"BSE record date {row['record_date_bse']}"
                    ),
                    nse_event_id=nse_event.event_id,
                    bse_event_id=bse_event.event_id,
                )
            )

        # Rule 2: Ratio Match
        ratio_diff = abs(row["ratio_multiplier_nse"] - row["ratio_multiplier_bse"])
        if ratio_diff > RATIO_TOLERANCE:
            row_anomalies.append(
                AnomalyRecord(
                    isin=isin,
                    conflict_type=ConflictType.RATIO_MISMATCH,
                    description=(
                        f"NSE ratio {row['ratio_multiplier_nse']} conflicts with "
                        f"BSE ratio {row['ratio_multiplier_bse']}"
                    ),
                    nse_event_id=nse_event.event_id,
                    bse_event_id=bse_event.event_id,
                )
            )

        if row_anomalies:
            anomalies.extend(row_anomalies)
            status = (
                EventStatus.ANOMALY_DATE_MISMATCH
                if any(a.conflict_type == ConflictType.DATE_MISMATCH for a in row_anomalies)
                else EventStatus.ANOMALY_RATIO_MISMATCH
            )
            nse_event.status = status
            bse_event.status = status
            verified.append(nse_event)
            verified.append(bse_event)
        else:
            nse_event.status = EventStatus.VERIFIED
            bse_event.status = EventStatus.VERIFIED
            verified.append(nse_event)
            verified.append(bse_event)

    # ISINs present on only one exchange: nothing to cross-check against.
    for _, row in merged[merged["_merge"] != "both"].iterrows():
        if row["isin"] in matched_isins:
            continue
        if pd.notna(row.get("event_id_nse")):
            verified.append(nse_by_id[row["event_id_nse"]])
        elif pd.notna(row.get("event_id_bse")):
            verified.append(bse_by_id[row["event_id_bse"]])

    return verified, anomalies
