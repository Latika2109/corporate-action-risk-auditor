"""Seed the local DB with mock BrokerHoldings for demo purposes.

Run: python -m app.seed
"""
from __future__ import annotations

from app.models.db import BrokerHoldingORM, SessionLocal, init_db
from app.models.schemas import new_id

MOCK_HOLDINGS = [
    {"client_id": "ACC_992811", "isin": "INE123456789", "shares": 100, "unit_value": 2500.0},
    {"client_id": "ACC_992812", "isin": "INE123456789", "shares": 50, "unit_value": 2500.0},
    {"client_id": "ACC_992813", "isin": "INE987654321", "shares": 200, "unit_value": 850.0},
    {"client_id": "ACC_992814", "isin": "INE555555555", "shares": 75, "unit_value": 1200.0},
]


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        existing = db.query(BrokerHoldingORM).count()
        if existing > 0:
            print(f"broker_holdings already has {existing} rows, skipping seed")
            return
        for h in MOCK_HOLDINGS:
            db.add(BrokerHoldingORM(holding_id=new_id(), **h))
        db.commit()
        print(f"seeded {len(MOCK_HOLDINGS)} mock broker holdings")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
