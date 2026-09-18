"""Smoke tests for the ingestion, validation, and simulation logic.

Run: pytest tests/ -v   (from repo root, with backend/ on PYTHONPATH)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.core.ingestion import ingest_csv_bytes, ingest_text_dump
from app.core.simulation import simulate_holdings_for_event
from app.core.validation import cross_validate
from app.models.schemas import BrokerHolding, Exchange


NSE_CSV = b"""isin,symbol,action_type,ratio,ex_date,record_date
INE123456789,RELIANCE,Stock Split,1:10,2026-10-14,2026-10-16
,MYSTERY_CO,Dividend,1,2026-11-10,2026-11-12
INE444555666,WIPRO,Split,1:2,not-a-date,2026-11-15
"""

BSE_CSV = b"""isin_code,scrip_name,purpose,split_ratio,exdate,book_closure_date
INE123456789,RELIANCE,Stock Split,1:10,14-10-2026,17-10-2026
"""


def test_ingestion_quarantines_missing_isin_and_bad_date():
    events, quarantined = ingest_csv_bytes(NSE_CSV, Exchange.NSE, "nse.csv")
    assert len(events) == 1
    assert len(quarantined) == 2
    reasons = " ".join(q.reason for q in quarantined)
    assert "ISIN" in reasons
    assert "ex_date" in reasons


def test_cross_validation_flags_date_mismatch():
    nse_events, _ = ingest_csv_bytes(NSE_CSV, Exchange.NSE, "nse.csv")
    bse_events, _ = ingest_csv_bytes(BSE_CSV, Exchange.BSE, "bse.csv")
    verified, anomalies = cross_validate(nse_events, bse_events)
    assert len(anomalies) == 1
    assert anomalies[0].conflict_type.value == "DATE_MISMATCH"


def test_text_dump_parses_and_quarantines():
    text = (
        "ISIN: INE777888999\nRatio: 1-for-10\n"
        "Ex-Date: 12-Dec-2026\nRecord Date: 14-Dec-2026\n\n"
        "this block has no isin or dates and should be quarantined"
    )
    events, quarantined = ingest_text_dump(text, Exchange.NSE, "dump.txt")
    assert len(events) == 1
    assert len(quarantined) == 1


def test_bonus_ratio_adds_to_existing_holding_not_replaces_it():
    csv = b"""isin,symbol,action_type,ratio,ex_date,record_date
INE987654321,TCS,Bonus,1:1,2026-10-20,2026-10-22
"""
    events, quarantined = ingest_csv_bytes(csv, Exchange.NSE, "nse.csv")
    assert not quarantined
    assert events[0].ratio_multiplier == 2.0


def test_shadow_simulation_applies_ratio():
    nse_events, _ = ingest_csv_bytes(NSE_CSV, Exchange.NSE, "nse.csv")
    event = nse_events[0]
    event.status = "VERIFIED"
    holdings = [BrokerHolding(client_id="ACC_1", isin=event.isin, shares=100)]
    entries = simulate_holdings_for_event(event, holdings)
    assert len(entries) == 1
    assert entries[0].simulated_post_event_shares == 1000
