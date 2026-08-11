from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models, schemas
from .account import get_settings, positions_value, resolve_equity, suggested_shares
from .config import ALLOWED_ORIGINS, API_KEY, DEMO_MODE, READ_ONLY
from .database import Base, engine, get_db
from .security import require_write_access
from .timeutil import utcnow

Base.metadata.create_all(bind=engine)


def announce_posture() -> None:
    """Says out loud which database and write policy are in effect.

    Both are set by environment variables, so getting them wrong is silent
    by nature: an instance serving real holdings with writes wide open
    looks exactly like one that's correctly configured. Printing it at
    startup means a misconfiguration is visible in the first line of the
    log rather than after something has gone wrong.
    """
    db = "demo (fake data)" if DEMO_MODE else "real"
    if READ_ONLY:
        writes = "BLOCKED (READ_ONLY=true)"
    elif API_KEY:
        writes = "require X-API-Key"
    else:
        writes = "OPEN — no API_KEY set, safe only if this is localhost-only"

    print(f"[api] database: {db} | writes: {writes}")
    print(f"[api] CORS allow_origins: {ALLOWED_ORIGINS}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The lifespan context is the non-deprecated replacement for
    # @app.on_event("startup").
    announce_posture()
    yield


app = FastAPI(title="Stock Signal Dashboard API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _signal_out(sig: models.Signal, equity: float, risk_fraction: float) -> schemas.SignalOut:
    """Serializes a stored signal, but recomputes `suggested_shares` from
    current account settings rather than serving the value the pipeline
    happened to store. Sizing depends on how much money you have, so it
    has to reflect the account as it is now — not as it was at the last
    daily run. See app/account.py."""
    out = schemas.SignalOut.model_validate(sig)
    out.suggested_shares = suggested_shares(sig.stop_distance, equity, risk_fraction)
    return out


def _holding_out(
    h: models.Holding,
    sig: Optional[models.Signal],
    equity: float,
    risk_fraction: float,
) -> schemas.HoldingOut:
    return schemas.HoldingOut(
        ticker=h.ticker,
        shares=h.shares,
        added_at=h.added_at,
        signal=_signal_out(sig, equity, risk_fraction) if sig else None,
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "demo_mode": DEMO_MODE}


@app.get("/api/holdings", response_model=List[schemas.HoldingOut])
def list_holdings(db: Session = Depends(get_db)):
    """Every current holding with its latest exit/entry signal attached —
    this is the primary view: exit-flagged holdings surface here."""
    equity, _ = resolve_equity(db)
    risk = get_settings(db).risk_fraction

    holdings = db.query(models.Holding).order_by(models.Holding.ticker).all()
    out = []
    for h in holdings:
        sig = db.query(models.Signal).filter(models.Signal.ticker == h.ticker).first()
        out.append(_holding_out(h, sig, equity, risk))
    return out


@app.post(
    "/api/holdings",
    response_model=schemas.HoldingOut,
    status_code=201,
    dependencies=[Depends(require_write_access)],
)
def add_holding(payload: schemas.HoldingCreate, db: Session = Depends(get_db)):
    ticker = payload.ticker.strip().upper()
    if not ticker:
        raise HTTPException(400, "ticker is required")

    existing = db.query(models.Holding).filter(models.Holding.ticker == ticker).first()
    if existing:
        raise HTTPException(409, f"{ticker} is already in holdings")

    h = models.Holding(ticker=ticker, shares=payload.shares, added_at=utcnow())
    db.add(h)
    db.commit()
    db.refresh(h)

    # Resolved after the commit: the new holding is itself part of equity.
    equity, _ = resolve_equity(db)
    sig = db.query(models.Signal).filter(models.Signal.ticker == ticker).first()
    return _holding_out(h, sig, equity, get_settings(db).risk_fraction)


@app.patch(
    "/api/holdings/{ticker}",
    response_model=schemas.HoldingOut,
    dependencies=[Depends(require_write_access)],
)
def update_holding(ticker: str, payload: schemas.HoldingUpdate, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    h = db.query(models.Holding).filter(models.Holding.ticker == ticker).first()
    if not h:
        raise HTTPException(404, f"{ticker} not found in holdings")

    if payload.shares is not None:
        h.shares = payload.shares
    db.commit()
    db.refresh(h)

    equity, _ = resolve_equity(db)
    sig = db.query(models.Signal).filter(models.Signal.ticker == ticker).first()
    return _holding_out(h, sig, equity, get_settings(db).risk_fraction)


@app.delete(
    "/api/holdings/{ticker}",
    status_code=204,
    dependencies=[Depends(require_write_access)],
)
def remove_holding(ticker: str, db: Session = Depends(get_db)):
    ticker = ticker.upper()
    h = db.query(models.Holding).filter(models.Holding.ticker == ticker).first()
    if not h:
        raise HTTPException(404, f"{ticker} not found in holdings")
    db.delete(h)
    db.commit()
    return None


@app.get("/api/entry-candidates", response_model=List[schemas.EntryCandidateOut])
def entry_candidates(limit: int = 10, db: Session = Depends(get_db)):
    """Strongest fresh entry signals across the scan universe, excluding
    anything already held. Ranked by how many ATRs above the breakout
    level the close printed — a rough proxy for signal strength."""
    limit = max(1, min(limit, 10))
    equity, _ = resolve_equity(db)
    risk = get_settings(db).risk_fraction
    held = {t for (t,) in db.query(models.Holding.ticker).all()}

    rows = db.query(models.Signal).filter(models.Signal.entry_signal == True).all()  # noqa: E712

    scored = []
    for r in rows:
        if r.ticker in held:
            continue
        strength = None
        if r.atr and r.donchian_high is not None and r.atr > 0:
            strength = (r.close - r.donchian_high) / r.atr
        scored.append((strength if strength is not None else 0.0, r))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[:limit]

    return [
        schemas.EntryCandidateOut(
            ticker=r.ticker,
            close=r.close,
            donchian_high=r.donchian_high,
            atr=r.atr,
            suggested_shares=suggested_shares(r.stop_distance, equity, risk),
            breakout_strength=strength,
            as_of_date=r.as_of_date,
        )
        for strength, r in top
    ]


def _account_out(db: Session) -> schemas.AccountOut:
    settings = get_settings(db)
    equity, source = resolve_equity(db)
    value, unvalued = positions_value(db)
    return schemas.AccountOut(
        equity=equity,
        equity_source=source,
        positions_value=value,
        cash_balance=settings.cash_balance or 0.0,
        risk_fraction=settings.risk_fraction,
        equity_override=settings.equity_override,
        risk_dollars=settings.risk_fraction * equity,
        unvalued_holdings=unvalued,
    )


@app.get("/api/account", response_model=schemas.AccountOut)
def get_account(db: Session = Depends(get_db)):
    """Account equity and the inputs behind it. Equity drives every
    `suggested_shares` figure the API returns."""
    return _account_out(db)


@app.patch(
    "/api/account",
    response_model=schemas.AccountOut,
    dependencies=[Depends(require_write_access)],
)
def update_account(payload: schemas.AccountUpdate, db: Session = Depends(get_db)):
    """Updates account settings. Takes effect immediately — position sizes
    are computed on read, so no pipeline re-run is needed.

    Uses exclude_unset so that omitting a field leaves it alone while
    sending it as null clears it. That distinction matters for
    `equity_override`: null is the only way back to the derived figure,
    and a plain `is not None` check could never express it.
    """
    settings = get_settings(db)
    fields = payload.model_dump(exclude_unset=True)

    for key in ("cash_balance", "risk_fraction", "equity_override"):
        if key in fields:
            setattr(settings, key, fields[key])

    # Guard the non-nullable columns: `{"cash_balance": null}` is a
    # well-formed PATCH that would otherwise write NULL into a NOT NULL
    # column and fail at commit with an opaque IntegrityError.
    if settings.cash_balance is None:
        settings.cash_balance = 0.0
    if settings.risk_fraction is None:
        settings.risk_fraction = 0.01

    settings.updated_at = utcnow()
    db.commit()
    return _account_out(db)


@app.get("/api/status", response_model=schemas.StatusOut)
def status(db: Session = Depends(get_db)):
    """When signals were last computed, so the frontend can flag stale data."""
    last = db.query(func.max(models.Signal.updated_at)).scalar()
    return schemas.StatusOut(demo_mode=DEMO_MODE, signals_last_updated=last)
