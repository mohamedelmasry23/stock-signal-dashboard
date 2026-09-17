"""Research harnesses.

Code in here measures strategy ideas; it is not part of the running app.
Nothing under `app/` may import it. The reverse is fine and expected —
a research harness that reimplements a live rule stops measuring the live
system, which is the mistake `app/signals.py` exists to prevent.
"""
