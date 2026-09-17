"""Codified liquidity-sweep reversal (the TJR/ICT premise), as research.

See `research/README.md` at the repository root for what every previous
experiment found, and this package's `params.py` for every threshold this
one exposes.

The one thing to read before editing anything here is the "known_at"
contract in `bars.py`. It is this package's equivalent of the d/x split in
`app/backtest/engine.py`: the single structural defence against look-ahead,
and the thing most likely to be broken by a well-meaning change.
"""
