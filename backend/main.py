import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.security import DesktopSessionAuthMiddleware
from backend.runtime_service import exclusive_backend, publish_session
from backend.worker import service as worker_service

from backend.config import SHARED_MEDIA_DIR, MANAGER_DIST_DIR
from backend.scheduler.background import setup_background_tasks, shutdown_background_tasks
from backend.services.automation_service import (
    recover_stale_queue_executions,
    active_automation_tasks,
    cleanup_orphaned_automation_containers,
)
from backend.services import container_lifecycle
from backend.services.queue_service import load_posting_queue, save_posting_queue
from backend.services.ocr_worker_service import start_shared_ocr_worker, stop_shared_ocr_worker

from backend.routers import (
    settings,
    profiles,
    evidence,
    proxies,
    brains,
    automation,
    queue,
    media,
    ai,
    support,
    system,
    worker,
    live,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    with exclusive_backend():
        session_path = None
        try:
            # Startup
            # Add the bundled Live family to the existing Brain manager without
            # overwriting user-installed workflow versions or its catalog.
            from automation.engine.live_brain import install_bundled_live_brain
            from backend.config import AUTOMATION_RUNNER, ROOT_DIR
            install_bundled_live_brain(AUTOMATION_RUNNER.parent, ROOT_DIR / 'automation')
            recover_stale_queue_executions("manager_startup_stale_lease")
            container_lifecycle.SHUTTING_DOWN = False
            legacy_queue = load_posting_queue()
            container_lifecycle.import_legacy_owners(legacy_queue)
            save_posting_queue(legacy_queue)
            await cleanup_orphaned_automation_containers()
            shared_ocr = start_shared_ocr_worker()
            setup_background_tasks()
            print(f"FastAPI Backend started successfully. Shared OCR: {shared_ocr.get('url') or 'local fallback'}")
            await worker_service.start()
            session_path = publish_session()
            yield
        finally:
            await worker_service.stop()
            # Shutdown
            container_lifecycle.SHUTTING_DOWN = True
            shutdown_background_tasks()
            await cleanup_orphaned_automation_containers(force=True)
            stop_shared_ocr_worker()
            print("FastAPI Backend shutdown complete.")
            if session_path:
                session_path.unlink(missing_ok=True)

app = FastAPI(
    title="Isolated Browser Manager",
    description="Anti-Bot Browser Automation & Multi-Profile Manager API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(DesktopSessionAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
        "http://localhost:5173",
        "http://127.0.0.1:1420",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Content-Type", "X-Manager-Token"],
)

# Register API Routers
app.include_router(settings.router)
app.include_router(profiles.router)
app.include_router(evidence.router)
app.include_router(proxies.router)
app.include_router(brains.router)
app.include_router(automation.router)
app.include_router(queue.router)
app.include_router(media.router)
app.include_router(ai.router)
app.include_router(support.router)
app.include_router(system.router)
app.include_router(worker.router)
app.include_router(live.router)

# Mount /shared_media static directory
if not SHARED_MEDIA_DIR.exists():
    SHARED_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/shared_media", StaticFiles(directory=str(SHARED_MEDIA_DIR)), name="shared_media")

# SPA fallback if dist directory exists
if MANAGER_DIST_DIR.exists():
    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        target = MANAGER_DIST_DIR / full_path
        if target.exists() and target.is_file():
            return FileResponse(target)
        index_file = MANAGER_DIST_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"error": "Dashboard not built"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "3001"))
    uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
