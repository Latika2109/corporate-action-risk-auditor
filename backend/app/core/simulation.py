"""Phase 3: Shadow Ledger Simulator.

Applies a VERIFIED corporate action's ratio to mock BrokerHoldings and
computes the expected post-event share balance, entirely in a sandbox —
no live Demat/ledger writes occur here.
"""
from __future__ import annotations

from app.models.schemas import (
    ApprovalStatus,
    BrokerHolding,
    ShadowSimulationEntry,
    StandardizedEvent,
)


def simulate_holdings_for_event(
    event: StandardizedEvent,
    holdings: list[BrokerHolding],
    depository_expected_shares: dict[str, float] | None = None,
) -> list[ShadowSimulationEntry]:
    """Apply event.ratio_multiplier to every holding matching event.isin.

    depository_expected_shares: optional {client_id: expected_shares} map
    representing the incoming depository credit, used to compute variance
    against our own simulation before market open.
    """
    depository_expected_shares = depository_expected_shares or {}
    entries: list[ShadowSimulationEntry] = []

    matching = [h for h in holdings if h.isin == event.isin]

    for holding in matching:
        pre_shares = holding.shares
        simulated_post = round(pre_shares * event.ratio_multiplier, 6)

        expected = depository_expected_shares.get(holding.client_id)
        variance = round(simulated_post - expected, 6) if expected is not None else 0.0

        entries.append(
            ShadowSimulationEntry(
                client_id=holding.client_id,
                isin=event.isin,
                event_id=event.event_id,
                pre_event_shares=pre_shares,
                simulated_post_event_shares=simulated_post,
                depository_expected_shares=expected,
                variance=variance,
                approval_status=ApprovalStatus.PENDING_AUTHORIZATION,
            )
        )

    return entries


def apply_sign_off(
    entry: ShadowSimulationEntry,
    decision: ApprovalStatus,
) -> ShadowSimulationEntry:
    """Human-in-the-loop authorization: mutate approval_status only.

    This never touches broker_holdings directly — a downstream ledger
    write job (out of scope for the MVP) would consume APPROVED entries.
    """
    entry.approval_status = decision
    return entry
