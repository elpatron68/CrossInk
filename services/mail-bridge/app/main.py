from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__
from .auth import require_device_token
from .config import Settings, get_settings
from .imap_worker import ImapWorker
from .mime_extract import safe_filename
from .store import ItemStore

LOG = logging.getLogger("mail-bridge")


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


def create_app(settings: Settings | None = None, store: ItemStore | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = store or ItemStore(settings.db_path, settings.items_dir)
    worker = ImapWorker(settings, store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        worker.start()
        yield
        worker.stop()

    app = FastAPI(title="CrossInk Mail Bridge", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.worker = worker
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/v1/health", response_model=HealthOut)
    def health() -> HealthOut:
        return HealthOut(
            status="ok",
            version=__version__,
            imap_configured=settings.imap_configured,
        )

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
        if not store.ack(item_id, user_id=user_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found")
        return JSONResponse({"ok": True, "id": item_id})

    return app


app = create_app()
