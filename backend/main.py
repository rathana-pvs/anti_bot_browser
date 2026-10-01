import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.security import DesktopSessionAuthMiddleware

from backend.config import SHARED_MEDIA_DIR, MANAGER_DIST_DIR
from backend.scheduler.background import setup_background_tasks, shutdown_background_tasks
from backend.services.automation_service import (
    recover_stale_queue_executions,
    active_automation_tasks,
)
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
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    recover_stale_queue_executions("manager_startup_stale_lease")
    shared_ocr = start_shared_ocr_worker()
    setup_background_tasks()
    print(f"FastAPI Backend started successfully. Shared OCR: {shared_ocr.get('url') or 'local fallback'}")
    yield
    # Shutdown
    shutdown_background_tasks()
    for task_record in active_automation_tasks.values():
        proc = task_record.get("process")
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
    stop_shared_ocr_worker()
    print("FastAPI Backend shutdown complete.")

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
    uvicorn.run("backend.main:app", host="127.0.0.1", port=port, reload=True)
