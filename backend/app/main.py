from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.models.db import init_db
from app.routers import exceptions, ingest, simulate

app = FastAPI(title="Corporate Action Risk Auditor", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router)
app.include_router(simulate.router)
app.include_router(exceptions.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
