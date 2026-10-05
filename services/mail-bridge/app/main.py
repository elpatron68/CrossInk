from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .auth import require_device_token
from .cleanup import OrphanCleanupWorker
from .config import Settings, get_settings
from .imap_worker import ImapWorker
from .mime_extract import safe_filename
from .rate_limit import SignupRateLimiter, client_ip
from .store import ItemStore

LOG = logging.getLogger("mail-bridge")

STATIC_DIR = Path(__file__).resolve().parent / "static"


class PendingItemOut(BaseModel):
    id: str
    filename: str
    bytes: int
    sha256: str
    content_type: str
    received_at: str


class HealthOut(BaseModel):
    status: str
    version: str
    imap_configured: bool


class CreateAccountIn(BaseModel):
    device_token: str = Field(min_length=64, max_length=64)


class CreateAccountOut(BaseModel):
    user_id: str
    mail_local: str
    email: str


def create_app(settings: Settings | None = None, store: ItemStore | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = store or ItemStore(
        settings.db_path,
        settings.items_dir,
        mail_local_prefix=settings.mail_local_prefix,
        mail_domain=settings.mail_domain,
        mail_local_length=settings.mail_local_length,
    )
    worker = ImapWorker(settings, store)
    orphan_cleanup = OrphanCleanupWorker(settings, store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        worker.start()
        orphan_cleanup.start()
        yield
        orphan_cleanup.stop()
        worker.stop()

    rate_limiter = SignupRateLimiter()
    app = FastAPI(title="CrossInk Mail Bridge", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.rate_limiter = rate_limiter
    app.state.worker = worker
    app.state.orphan_cleanup = orphan_cleanup
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/v1/health", response_model=HealthOut)
    def health() -> HealthOut:
        return HealthOut(
            status="ok",
            version=__version__,
            imap_configured=settings.imap_configured,
        )

    @app.post("/v1/accounts", response_model=CreateAccountOut, status_code=status.HTTP_201_CREATED)
    def create_account(body: CreateAccountIn, request: Request) -> CreateAccountOut:
        ip = client_ip(request, trust_proxy=settings.trust_proxy)
        if not rate_limiter.allow(ip, limit_per_hour=settings.signup_rate_limit_per_hour):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
            )
        token = body.device_token.strip()
        try:
            created = store.create_account(token)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return CreateAccountOut(user_id=created.user_id, mail_local=created.mail_local, email=created.email)

    @app.get("/v1/pending", response_model=list[PendingItemOut])
    def pending(user_id: str = Depends(require_device_token)) -> list[PendingItemOut]:
        # Refresh queue before listing so a One-Tap sync sees new mail without waiting for the timer.
        try:
            worker.poll_now()
        except Exception:
            LOG.exception("On-demand poll before pending failed")
        items = store.list_pending(user_id=user_id)
        return [
            PendingItemOut(
                id=item.id,
                filename=safe_filename(item.filename) or item.filename,
                bytes=item.bytes,
                sha256=item.sha256,
                content_type=item.content_type,
                received_at=item.received_at,
            )
            for item in items
        ]

    @app.get("/v1/items/{item_id}/content")
    def item_content(item_id: str, user_id: str = Depends(require_device_token)) -> FileResponse:
        item = store.get_item(item_id, user_id=user_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
        path = Path(item.path)
        if not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item file missing")
        safe_name = safe_filename(item.filename) or "book.epub"
        return FileResponse(
            path,
            media_type=item.content_type,
            filename=safe_name,
            headers={"X-Content-SHA256": item.sha256},
        )

    @app.post("/v1/items/{item_id}/ack")
    def item_ack(item_id: str, user_id: str = Depends(require_device_token)) -> JSONResponse:
        if not store.ack(item_id, user_id=user_id, delete_files=settings.delete_on_ack):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
        return JSONResponse({"ok": True, "id": item_id})

    @app.get("/", response_model=None)
    def pairing_page() -> HTMLResponse:
        index = STATIC_DIR / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pairing UI missing")
        html = index.read_text(encoding="utf-8")
        domain = (settings.plausible_domain or "").strip()
        if domain:
            script_url = (settings.plausible_script_url or "https://plausible.io/js/script.js").strip()
            safe_domain = domain.replace('"', "")
            safe_script = script_url.replace('"', "")
            snippet = (
                f'<script defer data-domain="{safe_domain}" src="{safe_script}"></script>\n'
                "  <!-- PLAUSIBLE -->"
            )
            html = html.replace("<!-- PLAUSIBLE -->", snippet, 1)
        return HTMLResponse(html)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


app = create_app()
