import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from backend.api.routes_v3 import router as v3_router
from backend.api.webhook_routes import router as webhook_router
from backend.models.database import init_db
from backend.core_engine.logger import get_logger

logger = get_logger("Main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=" * 50)
    logger.info("Starting Escrow Account Manager...")
    logger.info("=" * 50)
    
    # Run database migration first
    try:
        from migrate_all_columns import add_missing_columns
        logger.info("Running database migration...")
        add_missing_columns()
        logger.info("Migration completed")
    except Exception as e:
        logger.warning(f"Migration warning: {e}")
    
    await init_db()
    logger.info("Database initialized")
    
    yield
    
    logger.info("Shutting down...")
    from backend.api.routes_v2 import pyrogram_manager, telethon_manager
    
    if pyrogram_manager:
        await pyrogram_manager.disconnect_all()
        logger.info("Pyrogram sessions disconnected")
    
    if telethon_manager:
        await telethon_manager.disconnect_all()
        logger.info("Telethon sessions disconnected")
    
    logger.info("Shutdown complete")


app = FastAPI(
    title="Escrow Account Manager",
    description="Telegram Account Management System using Pyrogram and Telethon",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v3_router)
app.include_router(webhook_router)

@app.get("/")
async def root():
    return FileResponse("frontend/index_v3.html")

@app.get("/dashboard")
async def dashboard():
    return FileResponse("frontend/dashboard.html")

@app.get("/receive")
async def receive_page():
    return FileResponse("frontend/receive.html")

@app.get("/health")
@app.get("/actuator/health")
async def health_check():
    """Health check endpoint for monitoring"""
    return {
        "status": "healthy",
        "service": "Telegram Escrow Auditor",
        "version": "3.0.0"
    }

app.mount("/", StaticFiles(directory="frontend", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
