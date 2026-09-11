"""Serve the built frontend from the API process.

Only used in the single-container deployment, where there is one free web
service and it has to be everything: API, worker and static site. In the
Compose deployments nginx serves the bundle and this module is never mounted.

Two things are easy to get wrong here and both are handled explicitly:

* **The API's Content-Security-Policy would blank the page.** The middleware
  sets `default-src 'none'` on every response, which is correct for JSON and
  fatal for an HTML document that needs to load its own scripts. The document
  response sets a frontend policy of its own; the middleware uses `setdefault`
  and leaves it alone.

* **A catch-all route swallows 404s.** The SPA fallback has to answer every
  unknown path with `index.html` so a refresh on `/reviews/42` works -- but an
  unknown `/api/...` path must still be a JSON 404, not a 200 with a web page in
  it. The fallback refuses anything under the API prefixes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.logging import get_logger

logger = get_logger(__name__)

# What a document served from here may load. `'self'` for scripts and API
# calls; Google Fonts for the two typefaces; GitHub for avatars; `data:` images
# for anything the bundle inlines. Inline styles are allowed because React sets
# `style` attributes (the risk meter's width, for one) and Tailwind does not
# otherwise emit any -- inline *scripts* remain forbidden, which is the half
# that stops injected markup executing.
FRONTEND_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https://avatars.githubusercontent.com; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

# Paths that belong to the API and must never fall through to index.html.
RESERVED_PREFIXES = ("api/", "health", "docs", "redoc", "openapi.json")


def mount_spa(app: FastAPI, static_dir: Path) -> None:
    """Serve `static_dir` as a single-page app, after every API route."""
    index = static_dir / "index.html"
    if not index.is_file():
        # Loud rather than a mysterious 404 on every page: the most likely
        # cause is a Docker build that skipped the frontend stage.
        raise RuntimeError(f"DEVPILOT_STATIC_DIR is {static_dir} but it has no index.html.")

    assets = static_dir / "assets"
    if assets.is_dir():
        # Hashed filenames, so they can be cached for a year; nginx.conf does
        # the same in the Compose deployment.
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(request: Request, full_path: str) -> FileResponse:
        if full_path.startswith(RESERVED_PREFIXES):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")

        # A real file at the root of the bundle (favicon, manifest) is served as
        # itself. Anything else is a client-side route and gets the document.
        candidate = static_dir / full_path
        if full_path and candidate.is_file() and candidate.resolve().is_relative_to(static_dir):
            return FileResponse(candidate)

        return FileResponse(
            index,
            headers={
                "Content-Security-Policy": FRONTEND_CONTENT_SECURITY_POLICY,
                # index.html must never be cached, or clients keep a document
                # that references asset hashes deleted by the last deploy.
                "Cache-Control": "no-cache",
            },
        )

    logger.info("spa.mounted", static_dir=str(static_dir))
