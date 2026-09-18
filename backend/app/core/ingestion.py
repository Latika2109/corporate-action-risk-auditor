"""Phase 1: Ingestion & Normalization Pipeline.

Ingests raw NSE/BSE corporate-action CSVs (and mock unstructured text
circulars), coerces every row into the canonical `StandardizedEvent`
schema, and routes anything unparseable into the Quarantine Gateway
instead of crashing the pipeline.
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Optional

import pandas as pd
from pydantic import ValidationError

from app.models.schemas import (
    ActionType,
    Exchange,
    EventStatus,
    QuarantinedRecord,
    StandardizedEvent,
)

# ---------------------------------------------------------------------------
# Canonical column aliases: raw exchange dumps use inconsistent headers.
# Map every known variant (including BSE's mutated layouts) to canonical names.
# ---------------------------------------------------------------------------
COLUMN_ALIASES = {
    "isin": {"isin", "isin_code", "isin no", "isin_no", "security_isin"},
    "symbol": {"symbol", "security_name", "company_name", "scrip_name", "scrip"},
    "action_type": {"action_type", "purpose", "ca_type", "subject", "corp_action"},
    "ratio": {"ratio", "face_value_ratio", "split_ratio", "bonus_ratio", "ex_ratio"},
    "ex_date": {"ex_date", "exdate", "ex-date", "ex date"},
    "record_date": {"record_date", "recdate", "rec_date", "record date", "book_closure_date"},
}

DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y"]

# Matches ratios like "1:10", "10:1", "1-for-10", "1 for 5"
RATIO_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(?::|-for-|for)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)

ACTION_KEYWORDS = {
    ActionType.SPLIT: ["split", "sub-division", "subdivision"],
    ActionType.BONUS: ["bonus"],
    ActionType.DIVIDEND: ["dividend"],
    ActionType.MERGER: ["merger", "amalgamation", "scheme of arrangement"],
    ActionType.RIGHTS: ["rights"],
}


def _normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Rename whatever headers the source file has to canonical names."""
    reverse_map: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            reverse_map[alias.strip().lower()] = canonical

    rename_map = {}
    for col in df.columns:
        key = str(col).strip().lower().replace("__", "_")
        if key in reverse_map:
            rename_map[col] = reverse_map[key]
    return df.rename(columns=rename_map)


def _parse_date(value) -> Optional[datetime]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "-"}:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = pd.to_datetime(text, errors="raise", dayfirst=True)
        return parsed.to_pydatetime()
    except Exception:
        return None


def _parse_ratio(value, action_type: ActionType = ActionType.UNKNOWN) -> Optional[float]:
    """Parse ratio text into a single multiplier applied to existing shares.

    Convention differs by action type:
      - SPLIT: '1:10' / '1-for-10' means 1 old share becomes 10 new shares
               -> multiplier = new/old = 10.0
      - BONUS: '2:1' means 2 *additional* shares issued per 1 held
               -> multiplier = 1 + (new/old) = 3.0
    A plain numeric value (no colon/for) is treated as an already-computed
    multiplier and passed through as-is regardless of action type.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "-", ""}:
        return None

    match = RATIO_PATTERN.search(text)
    if match:
        old, new = float(match.group(1)), float(match.group(2))
        if old == 0:
            return None
        ratio = new / old
        if action_type == ActionType.BONUS:
            ratio = 1.0 + ratio
        return round(ratio, 6)

    try:
        numeric = float(text)
        return numeric if numeric > 0 else None
    except ValueError:
        return None


def _infer_action_type(value) -> ActionType:
    text = str(value or "").strip().lower()
    for action, keywords in ACTION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return action
    return ActionType.UNKNOWN


def _row_to_event(row: pd.Series, exchange: Exchange) -> StandardizedEvent:
    """Attempt to build a StandardizedEvent from one normalized row.

    Raises ValueError/ValidationError on any unparseable field; the caller
    is responsible for catching these and routing to quarantine.
    """
    isin = str(row.get("isin", "")).strip().upper()
    if not isin or isin.lower() in {"nan", "none"}:
        raise ValueError("missing or empty ISIN")

    ex_date = _parse_date(row.get("ex_date"))
    record_date = _parse_date(row.get("record_date"))
    if ex_date is None:
        raise ValueError(f"unparseable ex_date: '{row.get('ex_date')}'")
    if record_date is None:
        raise ValueError(f"unparseable record_date: '{row.get('record_date')}'")

    action_type = _infer_action_type(row.get("action_type"))

    ratio = _parse_ratio(row.get("ratio"), action_type)
    if ratio is None:
        raise ValueError(f"unparseable ratio: '{row.get('ratio')}'")

    return StandardizedEvent(
        isin=isin,
        exchange=exchange,
        action_type=action_type,
        ratio_multiplier=ratio,
        ex_date=ex_date.date(),
        record_date=record_date.date(),
        status=EventStatus.PENDING_VALIDATION,
        raw_source_row=row.to_json(),
    )


def ingest_csv_bytes(
    raw_bytes: bytes,
    exchange: Exchange,
    source_file: str,
) -> tuple[list[StandardizedEvent], list[QuarantinedRecord]]:
    """Ingest a raw CSV (or CSV-like text dump) into verified events + quarantine.

    Never raises on malformed content — worst case, every row is quarantined.
    """
    verified: list[StandardizedEvent] = []
    quarantined: list[QuarantinedRecord] = []

    try:
        df = pd.read_csv(io.BytesIO(raw_bytes), dtype=str, keep_default_na=True)
    except Exception as exc:
        quarantined.append(
            QuarantinedRecord(
                source_file=source_file,
                exchange=exchange,
                raw_row=raw_bytes[:500].decode("utf-8", errors="replace"),
                reason=f"file-level parse failure: {exc}",
            )
        )
        return verified, quarantined

    if df.empty:
        quarantined.append(
            QuarantinedRecord(
                source_file=source_file,
                exchange=exchange,
                raw_row="",
                reason="file contained no rows",
            )
        )
        return verified, quarantined

    df = _normalize_headers(df)
    df = df.dropna(how="all")
    df.columns = [str(c).strip().lower() for c in df.columns]

    required = {"isin", "ex_date", "record_date", "ratio"}
    missing_cols = required - set(df.columns)
    if missing_cols:
        quarantined.append(
            QuarantinedRecord(
                source_file=source_file,
                exchange=exchange,
                raw_row=",".join(df.columns),
                reason=f"file missing required columns: {sorted(missing_cols)}",
            )
        )
        return verified, quarantined

    for _, row in df.iterrows():
        row = row.where(pd.notna(row), None)
        try:
            event = _row_to_event(row, exchange)
            verified.append(event)
        except (ValueError, ValidationError) as exc:
            quarantined.append(
                QuarantinedRecord(
                    source_file=source_file,
                    exchange=exchange,
                    raw_row=row.to_json(),
                    reason=str(exc),
                )
            )

    return verified, quarantined


def ingest_text_dump(
    raw_text: str,
    exchange: Exchange,
    source_file: str,
) -> tuple[list[StandardizedEvent], list[QuarantinedRecord]]:
    """Best-effort parser for unstructured circular text dumps.

    Looks for ISIN / date / ratio patterns line-by-line. Anything that
    doesn't yield a complete triple is quarantined line-by-line so one
    bad circular doesn't block the rest of the batch.
    """
    verified: list[StandardizedEvent] = []
    quarantined: list[QuarantinedRecord] = []

    isin_pattern = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")
    blocks = re.split(r"\n\s*\n", raw_text.strip()) if raw_text.strip() else []

    if not blocks:
        quarantined.append(
            QuarantinedRecord(
                source_file=source_file,
                exchange=exchange,
                raw_row="",
                reason="empty text dump",
            )
        )
        return verified, quarantined

    date_token_pattern = re.compile(
        r"\b\d{1,2}[-/](?:\d{1,2}|[A-Za-z]{3,9})[-/]\d{2,4}\b"
    )

    for block in blocks:
        isin_match = isin_pattern.search(block)
        block_action_type = _infer_action_type(block)
        ratio = _parse_ratio(block, block_action_type)
        date_tokens = date_token_pattern.findall(block)
        parsed_dates = [d for d in (_parse_date(tok) for tok in date_tokens) if d is not None]

        try:
            if not isin_match:
                raise ValueError("no ISIN found in text block")
            if ratio is None:
                raise ValueError("no parseable ratio found in text block")
            if len(parsed_dates) < 2:
                raise ValueError(
                    f"expected ex_date + record_date, found {len(parsed_dates)} parseable date(s)"
                )

            ex_date, record_date = parsed_dates[0], parsed_dates[1]

            event = StandardizedEvent(
                isin=isin_match.group(1),
                exchange=exchange,
                action_type=block_action_type,
                ratio_multiplier=ratio,
                ex_date=ex_date.date(),
                record_date=record_date.date(),
                status=EventStatus.PENDING_VALIDATION,
                raw_source_row=block.strip()[:1000],
            )
            verified.append(event)
        except (ValueError, ValidationError) as exc:
            quarantined.append(
                QuarantinedRecord(
                    source_file=source_file,
                    exchange=exchange,
                    raw_row=block.strip()[:1000],
                    reason=str(exc),
                )
            )

    return verified, quarantined
