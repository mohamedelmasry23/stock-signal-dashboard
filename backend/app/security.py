"""
Write protection for the mutating endpoints.

Three postures, chosen by environment rather than by code path:

  READ_ONLY=true   every write returns 403. This is the public-demo
                   setting: the dashboard stays fully browsable but no
                   visitor can change what everyone else sees.

  API_KEY=<value>  writes require a matching `X-API-Key` header. For a
                   private deploy of your real instance.

  neither set      writes are open. Correct for localhost-only use, and
                   the reason app/main.py prints its posture at startup
                   instead of leaving it to be discovered later.

On the API key in a browser: a key shipped in a frontend bundle is not
secret — anyone can read it out of the JavaScript. It raises the bar
against drive-by scanners hitting an exposed API, and that's all. Don't
treat it as a substitute for real authentication if this ever holds data
that matters to someone other than you; the honest answer for a public
demo is READ_ONLY, not a key.
"""

import secrets

from fastapi import Header, HTTPException

from .config import API_KEY, READ_ONLY


def require_write_access(x_api_key: str = Header(default="")) -> None:
    """FastAPI dependency guarding every mutating endpoint."""
    if READ_ONLY:
        raise HTTPException(
            403,
            "This instance is read-only. Holdings and account settings can't be "
            "changed here.",
        )

    if not API_KEY:
        return  # local dev: no key configured, writes open

    # Constant-time compare: a plain `!=` leaks how much of the key matched
    # through timing, which is enough to recover it byte by byte.
    if not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "Missing or invalid X-API-Key header.")
