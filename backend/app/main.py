import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, List, Literal, Optional

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Path
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import history, models, schemas, watch
from .account import get_settings, positions_value, resolve_equity, suggested_shares
from .quotes import get_quotes
from .config import (
    ALLOWED_ORIGINS,
    API_KEY,
    DEMO_MODE,
    READ_ONLY,
    is_point_in_time,
    is_rolling_membership,
)
from .database import Base, engine, get_db, sync_new_columns
from .security import require_write_access
from .signals import RSI_ENTRY_THRESHOLD
from .timeutil import utcnow

Base.metadata.create_all(bind=engine)
sync_new_columns()


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
    quote=None,
) -> schemas.HoldingOut:
    """Serializes a holding. When a live quote is supplied it drives the
    displayed value and day-change; the signal block stays end-of-day
    regardless, because those fields define the strategy."""
    price = quote.price if quote and quote.price is not None else (sig.close if sig else None)
    return schemas.HoldingOut(
        ticker=h.ticker,
        shares=h.shares,
        added_at=h.added_at,
        signal=_signal_out(sig, equity, risk_fraction) if sig else None,
        quote=(
            schemas.QuoteOut(
                price=quote.price,
                change_percent=quote.change_percent,
                previous_close=quote.previous_close,
                market_state=quote.market_state,
            )
            if quote
            else None
        ),
        market_value=(h.shares * price) if (h.shares is not None and price is not None) else None,
    )


def _live_prices(db: Session, live: bool) -> dict:
    """Live quotes for held tickers, or {} when not requested.

    Never raises: Yahoo being slow or down must degrade the dashboard to
    stored end-of-day figures, not break it."""
    if not live:
        return {}
    tickers = [t for (t,) in db.query(models.Holding.ticker).all()]
    try:
        return get_quotes(tickers)
    except Exception as e:  # noqa: BLE001 - a quote is never load-bearing
        print(f"[api] WARNING: live quote fetch failed ({e}); using stored closes.")
        return {}


def _quote_for(ticker: str, live: bool):
    """One live quote, or None.

    Same never-raise posture as `_live_prices`: Yahoo being down must cost
    the page its headline price, not the page. `_live_prices` can't be
    reused because it queries held tickers, and the detail page has to work
    for a ticker you don't own.
    """
    if not live:
        return None
    try:
        return get_quotes([ticker]).get(ticker)
    except Exception as e:  # noqa: BLE001 - a quote is never load-bearing
        print(f"[api] WARNING: quote fetch failed for {ticker} ({e}).")
        return None


def _col(series, digits: int = 4) -> List[Optional[float]]:
    """A pandas column as JSON-safe numbers.

    NaN is the trap here: `float('nan')` is not valid JSON and serializes
    as a bare `NaN` token that `JSON.parse` rejects outright, taking the
    whole page down rather than one value. Warm-up gaps must travel as
    null. Rounding on the way out is free size — no chart needs the last
    twelve digits of 123.45000000000002.
    """
    return [None if pd.isna(v) else round(float(v), digits) for v in series]


@app.get("/api/health")
def health():
    return {"status": "ok", "demo_mode": DEMO_MODE}


@app.get("/api/holdings", response_model=List[schemas.HoldingOut])
def list_holdings(live: bool = False, db: Session = Depends(get_db)):
    """Every current holding with its latest exit/entry signal attached —
    this is the primary view: exit-flagged holdings surface here.

    `?live=true` additionally fetches current quotes from Yahoo, which
    drive each holding's displayed value and day-change percentage. Exit
    and entry signals are unaffected: those come from completed daily
    closes, which is what keeps this the same system the backtest measured.
    """
    quotes = _live_prices(db, live)
    prices = {t: q.price for t, q in quotes.items() if q.price is not None}
    equity, _ = resolve_equity(db, prices)
    risk = get_settings(db).risk_fraction

    holdings = db.query(models.Holding).order_by(models.Holding.ticker).all()
    out = []
    for h in holdings:
        sig = db.query(models.Signal).filter(models.Signal.ticker == h.ticker).first()
        out.append(_holding_out(h, sig, equity, risk, quotes.get(h.ticker)))
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


def _rsi_signal_out(
    sig: models.RsiSignal, equity: float, risk_fraction: float
) -> schemas.RsiSignalOut:
    """As `_signal_out`, for the RSI strategy: sizing is recomputed from
    the account as it stands now rather than served from the value the
    pipeline stored last night."""
    out = schemas.RsiSignalOut.model_validate(sig)
    out.suggested_shares = suggested_shares(sig.stop_distance, equity, risk_fraction)
    return out


@app.get("/api/watch", response_model=schemas.WatchOut)
def watchlist(limit: int = 20, db: Session = Depends(get_db)):
    """Tickers trading above their 55-day Donchian high right now.

    NOT a signal, and never written to `signals`. A name here may fade
    before the close and never be flagged — that is the end-of-day rule
    working, not a miss. Nothing on this endpoint has a backtest behind it;
    the measured system is `/api/entry-candidates`, computed from completed
    daily closes.

    Measured over 52,087 breakouts, entering at the signal day's close
    rather than the next open was worth +0.04% on the 77% that cross
    intraday. So this exists to show, not to decide.

    Read-only. The scan that fills these tables is a scheduled job
    (`python3 -m app.watch`) — a ~40-second fetch across 547 tickers cannot
    sit on a page load.

    `limit` defaults to 20 rather than entry-candidates' 10 because the
    measured distribution is different: a median 24 names sit above their
    level on a given day, 67 at the 90th percentile, 275 at the observed
    maximum. Ten would truncate an ordinary day.
    """
    limit = max(1, min(limit, 50))

    scan = watch.latest_scan(db)
    now_et = watch.market_now_et()

    held = {t for (t,) in db.query(models.Holding.ticker).all()}
    flagged = {
        t for (t,) in db.query(models.Signal.ticker)
        .filter(models.Signal.entry_signal == True)  # noqa: E712
        .all()
    }

    rows = db.query(models.WatchItem).all()

    # Same key, same direction, same None handling as entry_candidates, so
    # the two panels can never order the same tickers differently.
    scored = [(r.atrs_above if r.atrs_above is not None else 0.0, r) for r in rows]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    items = [
        schemas.WatchItemOut(
            ticker=r.ticker,
            price=r.price,
            level=r.level,
            atr=r.atr,
            atrs_above=r.atrs_above,
            prev_close=r.prev_close,
            change_percent=(
                round((r.price - r.prev_close) / r.prev_close * 100, 4)
                if r.prev_close
                else None
            ),
            gap_open=bool(r.gap_open),
            # Held and already-flagged names are MARKED, not filtered — the
            # opposite of entry-candidates, which excludes holdings because
            # it is a buy list. This is an observation, and a position you
            # hold going back above its 55-day high is among the most
            # informative things on the page.
            held=r.ticker in held,
            already_flagged=r.ticker in flagged,
        )
        for _, r in scored[:limit]
    ]

    return schemas.WatchOut(
        items=items,
        scanned_at=scan.scanned_at if scan else None,
        session_date=scan.session_date if scan else None,
        market_open=watch.in_session(now_et),
        stale=watch.is_stale(scan, now_et),
        current_session=watch.is_current_session(scan, now_et),
        universe_size=scan.universe_size if scan else 0,
        fetched=scan.fetched if scan else 0,
        above_count=len(rows),
        truncated=len(rows) > limit,
    )


@app.get("/api/rsi/holdings", response_model=List[schemas.RsiHoldingOut])
def list_rsi_holdings(live: bool = False, db: Session = Depends(get_db)):
    """Your holdings with RSI(2) pullback signals attached.

    Same positions as `/api/holdings` — the difference is entirely in which
    of them is flagged for exit, because the two strategies exit on
    different conditions. A ticker can legitimately be an exit here and a
    hold there; that disagreement is information, not a bug, and neither
    page is authoritative over the other.
    """
    quotes = _live_prices(db, live)
    prices = {t: q.price for t, q in quotes.items() if q.price is not None}
    equity, _ = resolve_equity(db, prices)
    risk = get_settings(db).risk_fraction

    out = []
    for h in db.query(models.Holding).order_by(models.Holding.ticker).all():
        sig = db.query(models.RsiSignal).filter(models.RsiSignal.ticker == h.ticker).first()
        quote = quotes.get(h.ticker)
        price = quote.price if quote and quote.price is not None else (sig.close if sig else None)
        out.append(
            schemas.RsiHoldingOut(
                ticker=h.ticker,
                shares=h.shares,
                added_at=h.added_at,
                signal=_rsi_signal_out(sig, equity, risk) if sig else None,
                quote=(
                    schemas.QuoteOut(
                        price=quote.price,
                        change_percent=quote.change_percent,
                        previous_close=quote.previous_close,
                        market_state=quote.market_state,
                    )
                    if quote
                    else None
                ),
                market_value=(
                    h.shares * price if (h.shares is not None and price is not None) else None
                ),
            )
        )
    return out


@app.get("/api/rsi/entry-candidates", response_model=List[schemas.RsiEntryCandidateOut])
def rsi_entry_candidates(limit: int = 10, db: Session = Depends(get_db)):
    """The most oversold pullbacks in the scan universe, excluding anything
    already held.

    Ranked by how far RSI(2) printed *below* the entry trigger — the deeper
    the dip, the further the strategy expects it to snap back. That is the
    mean-reversion mirror of ranking Donchian candidates by how far above
    the breakout they closed.
    """
    limit = max(1, min(limit, 10))
    equity, _ = resolve_equity(db)
    risk = get_settings(db).risk_fraction
    held = {t for (t,) in db.query(models.Holding.ticker).all()}

    rows = (
        db.query(models.RsiSignal)
        .filter(models.RsiSignal.entry_signal == True)  # noqa: E712
        .all()
    )

    # Ascending RSI: most oversold first. Rows with no RSI at all sort last
    # rather than first, which a plain ascending sort on None would get
    # backwards.
    scored = [r for r in rows if r.ticker not in held]
    scored.sort(key=lambda r: (r.rsi is None, r.rsi if r.rsi is not None else 0.0))

    return [
        schemas.RsiEntryCandidateOut(
            ticker=r.ticker,
            close=r.close,
            rsi=r.rsi,
            sma_fast=r.sma_fast,
            sma_trend=r.sma_trend,
            atr=r.atr,
            suggested_shares=suggested_shares(r.stop_distance, equity, risk),
            oversold_depth=(RSI_ENTRY_THRESHOLD - r.rsi) if r.rsi is not None else None,
            as_of_date=r.as_of_date,
        )
        for r in scored[:limit]
    ]


def _account_out(db: Session, prices: Optional[dict] = None) -> schemas.AccountOut:
    settings = get_settings(db)
    equity, source = resolve_equity(db, prices)
    value, unvalued = positions_value(db, prices)
    return schemas.AccountOut(
        equity=equity,
        equity_source=source,
        positions_value=value,
        cash_balance=settings.cash_balance or 0.0,
        risk_fraction=settings.risk_fraction,
        equity_override=settings.equity_override,
        risk_dollars=settings.risk_fraction * equity,
        unvalued_holdings=unvalued,
        live=bool(prices),
    )


@app.get("/api/account", response_model=schemas.AccountOut)
def get_account(live: bool = False, db: Session = Depends(get_db)):
    """Account equity and the inputs behind it. Equity drives every
    `suggested_shares` figure the API returns.

    `?live=true` values holdings at current quotes rather than the last
    stored closes, so equity and the suggested sizes move intraday."""
    quotes = _live_prices(db, live)
    return _account_out(db, {t: q.price for t, q in quotes.items() if q.price is not None})


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


def _backtest_run_out(db: Session, run: models.BacktestRun) -> schemas.BacktestRunOut:
    points = (
        db.query(models.BacktestEquityPoint)
        .filter(models.BacktestEquityPoint.run_id == run.id)
        .order_by(models.BacktestEquityPoint.as_of_date)
        .all()
    )
    return schemas.BacktestRunOut(
        id=run.id,
        label=run.label or run.variant,
        universe=run.universe or "",
        strategy=run.strategy or "donchian",
        variant=run.variant,
        start_date=run.start_date,
        end_date=run.end_date,
        summary=json.loads(run.summary_json) if run.summary_json else {},
        benchmark_summary=(
            json.loads(run.benchmark_summary_json) if run.benchmark_summary_json else None
        ),
        equity=[
            schemas.EquityPointOut(
                date=p.as_of_date,
                strategy=p.strategy,
                benchmark=p.benchmark,
                exposure=p.exposure,
            )
            for p in points
        ],
    )


def _backtest_out(db: Session, strategy: str) -> schemas.BacktestOut:
    """The most recent backtest of each variant for one strategy.

    Filtered by strategy rather than served as one pile: each dashboard
    page shows its own system, and mixing them would put an RSI curve under
    a Donchian heading. Runs written before the `strategy` column existed
    are Donchian ones, hence the NULL fallback.
    """
    stored = [
        run
        for run in db.query(models.BacktestRun).order_by(models.BacktestRun.created_at.desc()).all()
        if (run.strategy or "donchian") == strategy
    ]

    def kind(run: models.BacktestRun) -> str:
        universe = run.universe or ""
        if is_rolling_membership(universe):
            return "membership"
        return "snapshot" if is_point_in_time(universe) else "current"

    def latest(variant: str, universe_kind: str) -> Optional[models.BacktestRun]:
        return next(
            (run for run in stored if run.variant == variant and kind(run) == universe_kind),
            None,
        )

    # A membership run — each company traded only while it was actually in
    # the index — is the headline whenever one is saved: it is the only S&P
    # universe a person could really have traded (decided
    # 2026-09-14). The today's-list run then steps back to a comparison
    # curve, `current_list`. Without one, today's list stays the headline.
    #
    # The comparison curves are chosen by kind, never by "newest of each
    # variant": that would let whichever was saved last displace the run
    # it exists to be compared against.
    membership = latest("faithful", "membership")
    if membership:
        faithful = membership
        atr_stop = latest("atr_stop", "membership")
        current_list = latest("faithful", "current")
    else:
        faithful = latest("faithful", "current")
        atr_stop = latest("atr_stop", "current")
        current_list = None
    point_in_time = latest("faithful", "snapshot")

    caveats: list[str] = []
    primary = faithful or atr_stop
    if primary:
        cfg = json.loads(primary.config_json) if primary.config_json else {}
        caveats.append(
            f"Costs modelled: {cfg.get('cost_bps', 0):.0f} bps commission + "
            f"{cfg.get('slippage_bps', 0):.0f} bps slippage per side."
        )
        caveats.append(
            "Fills happen on the bar after the signal — a signal from a close "
            "wasn't knowable until after that close."
        )
        caveats.append(
            "Prices include reinvested dividends; the benchmark is treated identically."
        )
        if membership:
            caveats.append(
                "Survivorship bias is reduced, not removed: these figures trade each company "
                "only while it was actually in the S&P 500, the closest to what could have been "
                "traded at the time. They still lean optimistic, because free price data is "
                "missing for many companies that were later removed, and those were "
                "disproportionately the failures. The run on today's list is shown only for "
                "comparison — it could buy companies before they joined."
            )
        elif "sp500" in (primary.universe or "") and point_in_time:
            caveats.append(
                "Survivorship bias is bracketed, not removed: the main run uses today's "
                "S&P 500 membership and so overstates, the point-in-time run freezes "
                "membership at 2016 and so understates. The honest reading is that the "
                "real result lies between the two strategy curves."
            )
        elif "sp500" in (primary.universe or ""):
            caveats.append(
                "Survivorship bias: the universe is today's S&P 500 membership, so only "
                "companies that survived and stayed in the index were tested. Real-time "
                "results would have been worse."
            )
        caveats.append(
            "Past performance of a simulated strategy is not a prediction. This is a "
            "measurement of a rule set over one historical window."
        )

    return schemas.BacktestOut(
        faithful=_backtest_run_out(db, faithful) if faithful else None,
        atr_stop=_backtest_run_out(db, atr_stop) if atr_stop else None,
        point_in_time=_backtest_run_out(db, point_in_time) if point_in_time else None,
        current_list=_backtest_run_out(db, current_list) if current_list else None,
        caveats=caveats,
    )


@app.get("/api/backtest", response_model=schemas.BacktestOut)
def get_backtest(db: Session = Depends(get_db)):
    """The Donchian breakout backtest, with equity curves.

    Read-only and unauthenticated like the rest of the GETs — a backtest
    contains no holdings and no account data, only what the strategy would
    have done on public price history.

    Returns empty rather than 404 when nothing has been run: the dashboard
    renders an explanatory empty state, which is more useful than an error
    for a feature that simply hasn't been used yet.
    """
    return _backtest_out(db, "donchian")


@app.get("/api/rsi/backtest", response_model=schemas.BacktestOut)
def get_rsi_backtest(db: Session = Depends(get_db)):
    """The RSI(2) pullback backtest. Same shape as `/api/backtest`, and
    strictly separate from it — neither strategy's runs can displace the
    other's."""
    return _backtest_out(db, "rsi")


def _empty_bars() -> schemas.TickerBarsOut:
    return schemas.TickerBarsOut(
        dates=[], open=[], high=[], low=[], close=[], volume=[],
        donchian_high=[], donchian_low=[], entry_index=[], exit_index=[],
    )


def _ratio(numerator, denominator) -> Optional[float]:
    """Guarded division for the two ATR-relative figures. None rather than
    zero when the inputs aren't there — a ticker with too little history
    for an ATR has no distance-to-exit, and printing 0.0 would read as
    'right at the trigger'."""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(float(numerator) / float(denominator), 4)


@app.get("/api/ticker/{ticker}", response_model=schemas.TickerDetailOut)
def ticker_detail(
    ticker: Annotated[str, Path(pattern=schemas.TICKER_PATTERN)],
    range: Literal["6M", "1Y", "2Y", "5Y", "MAX"] = history.DEFAULT_RANGE,
    live: bool = True,
    db: Session = Depends(get_db),
):
    """Daily bars with the Donchian levels drawn alongside them, plus the
    ticker's headline numbers — everything the detail page renders.

    Deliberately `def` rather than `async def`. `yf.Ticker().history` blocks
    for a second or more; under `async def` that would freeze the event loop
    for every other request in flight. A sync handler runs in Starlette's
    threadpool instead, which is what every other route here relies on too.

    `live` defaults to true, inverting `/api/holdings`' default. That one
    opts out so first paint never waits on Yahoo — but this endpoint is
    already waiting on Yahoo for the bars, so opting out would only produce
    a page missing its headline price and save nothing.

    The signal block comes from the stored end-of-day row whenever there is
    one. Yahoo's daily history includes the session in progress, and reading
    levels off a partial bar would print an exit on an intraday dip that
    recovers by the close — a different strategy from the one the backtest
    measured. See app/quotes.py, which draws the same line.
    """
    ticker = ticker.upper()

    hist = history.get_history(ticker)
    sig = db.query(models.Signal).filter(models.Signal.ticker == ticker).first()
    holding = db.query(models.Holding).filter(models.Holding.ticker == ticker).first()
    universe = db.query(models.Universe).filter(models.Universe.ticker == ticker).first()

    if hist is None and sig is None and holding is None and universe is None:
        raise HTTPException(404, f"No price history for {ticker}.")

    quote = _quote_for(ticker, live)
    prices = {ticker: quote.price} if quote and quote.price is not None else {}
    equity, _ = resolve_equity(db, prices)
    risk = get_settings(db).risk_fraction

    today = date.today()
    bars = _empty_bars()
    partial_last_bar = False
    computed_row = None
    computed_prev_close = None

    if hist is not None and not hist.frame.empty:
        full = hist.frame.copy()

        # Mark the bar a signal *fires* on, not every bar it stays true.
        # A Donchian entry is a crossing: once price is above the 55-day
        # high it usually stays there for a while, and a trending ticker
        # would otherwise carry a triangle on nearly every bar — which
        # says "still above the level", not "here is where you'd have
        # bought". Computed on the full frame so a run that began before
        # the visible window doesn't get re-marked at its left edge.
        for column, flag in (("EntrySignal", "_entry_at"), ("ExitSignal", "_exit_at")):
            on = full[column].fillna(False).astype(bool)
            full[flag] = on & ~on.shift(1, fill_value=False)

        last_bar_date = full.index[-1].date()
        partial_last_bar = last_bar_date >= today

        # The last bar that is actually a bar. Markers and any computed
        # levels come from here, never from a session still trading.
        completed = full[full.index.date < today] if partial_last_bar else full
        if not completed.empty:
            computed_row = completed.iloc[-1]
            if len(completed) >= 2:
                previous = completed["Close"].iloc[-2]
                computed_prev_close = None if pd.isna(previous) else round(float(previous), 4)
        completed_through = completed.index[-1].date() if not completed.empty else None

        window = history.slice_range(full, range)
        dates = [ts.date() for ts in window.index]

        def markers(column: str) -> List[int]:
            flags = window[column].tolist()
            return [
                i
                for i, on in enumerate(flags)
                if on and completed_through is not None and dates[i] <= completed_through
            ]

        bars = schemas.TickerBarsOut(
            dates=dates,
            open=_col(window["Open"]),
            high=_col(window["High"]),
            low=_col(window["Low"]),
            close=_col(window["Close"]),
            volume=_col(window["Volume"], digits=0),
            donchian_high=_col(window["DonchianHigh_N"]),
            donchian_low=_col(window["DonchianLow_M"]),
            entry_index=markers("_entry_at"),
            exit_index=markers("_exit_at"),
        )

    if sig is not None:
        # The stored row: the same figures the dashboard card shows, so the
        # two views can never disagree about whether a position is flagged.
        stats = schemas.TickerStatsOut(
            ticker=ticker,
            as_of_date=sig.as_of_date,
            close=sig.close,
            prev_close=sig.prev_close,
            donchian_high=sig.donchian_high,
            donchian_low=sig.donchian_low,
            atr=sig.atr,
            stop_distance=sig.stop_distance,
            entry_signal=bool(sig.entry_signal),
            exit_signal=bool(sig.exit_signal),
            signal_source="pipeline",
        )
    elif computed_row is not None:
        # A ticker outside the scan universe — typed into the URL, or held
        # before the next daily run reaches it. Derived here so the page
        # still says something, and labelled so it's clear it hasn't been
        # through a pipeline run.
        stats = schemas.TickerStatsOut(
            ticker=ticker,
            as_of_date=computed_row.name.date(),
            close=None if pd.isna(computed_row["Close"]) else round(float(computed_row["Close"]), 4),
            prev_close=computed_prev_close,
            donchian_high=None if pd.isna(computed_row["DonchianHigh_N"]) else round(float(computed_row["DonchianHigh_N"]), 4),
            donchian_low=None if pd.isna(computed_row["DonchianLow_M"]) else round(float(computed_row["DonchianLow_M"]), 4),
            atr=None if pd.isna(computed_row["ATR"]) else round(float(computed_row["ATR"]), 4),
            stop_distance=None if pd.isna(computed_row["StopDistance"]) else round(float(computed_row["StopDistance"]), 4),
            entry_signal=bool(computed_row["EntrySignal"]),
            exit_signal=bool(computed_row["ExitSignal"]),
            signal_source="computed",
        )
    else:
        stats = schemas.TickerStatsOut(ticker=ticker, signal_source="computed")

    stats.long_name = quote.long_name if quote else None
    stats.asset_class = universe.asset_class if universe else None
    stats.atrs_to_exit = _ratio(
        (stats.close - stats.donchian_low)
        if (stats.close is not None and stats.donchian_low is not None)
        else None,
        stats.atr,
    )
    stats.breakout_strength = _ratio(
        (stats.close - stats.donchian_high)
        if (stats.close is not None and stats.donchian_high is not None)
        else None,
        stats.atr,
    )
    # Resolved from live account equity, never read off the signal frame's
    # own Shares column — that one is computed against a placeholder equity
    # and doesn't compound. See app/account.py.
    stats.suggested_shares = suggested_shares(stats.stop_distance, equity, risk)

    price = quote.price if quote and quote.price is not None else stats.close
    shares = holding.shares if holding else None

    return schemas.TickerDetailOut(
        ticker=ticker,
        range=range,
        bars=bars,
        stats=stats,
        quote=(
            schemas.QuoteOut(
                price=quote.price,
                change_percent=quote.change_percent,
                previous_close=quote.previous_close,
                market_state=quote.market_state,
            )
            if quote
            else None
        ),
        held=holding is not None,
        shares=shares,
        market_value=(shares * price) if (shares is not None and price is not None) else None,
        partial_last_bar=partial_last_bar,
        history_available=hist is not None and not hist.frame.empty,
    )


@app.get("/api/status", response_model=schemas.StatusOut)
def status(db: Session = Depends(get_db)):
    """When each strategy's signals were last computed, so the frontend can
    flag stale data per page."""
    last = db.query(func.max(models.Signal.updated_at)).scalar()
    rsi_last = db.query(func.max(models.RsiSignal.updated_at)).scalar()
    return schemas.StatusOut(
        demo_mode=DEMO_MODE,
        signals_last_updated=last,
        rsi_signals_last_updated=rsi_last,
    )
