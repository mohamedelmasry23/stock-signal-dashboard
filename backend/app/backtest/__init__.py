"""
Backtesting: replay the dashboard's strategy over historical prices and
measure what would have happened.

Nothing in `app/main.py` imports this package, so the API's startup path
and `Base.metadata` are unaffected by anything in here.

The one rule that matters most: this package must never reimplement the
strategy. It calls `app.signals.compute_signal_frame()` exactly as the
live pipeline does. A backtest that reimplements its own signals stops
describing the system it's meant to be measuring the moment either side
changes.
"""

from .config import BacktestConfig
from .engine import BacktestResult, Trade, run_backtest

__all__ = ["BacktestConfig", "BacktestResult", "Trade", "run_backtest"]
