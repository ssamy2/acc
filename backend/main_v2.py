import sys
import os
import asyncio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../")))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from config import DEV_MODE, ALLOWED_ORIGINS, API_BASE_URL, FRONTEND_URL
from backend.api.routes import router as api_router
from backend.api.webhook_routes import router as webhook_router
from backend.api.auth import router as auth_router
from backend.api.sessions import router as sessions_router
from backend.api.admin import router as admin_router
from backend.api.delivery import router as delivery_router
from backend.api.audit import router as audit_router
from backend.models.database import init_db
from backend.core_engine.logger import get_logger

logger = get_logger("Main")

CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes


async def _periodic_cleanup():
    """Periodically clean up dead/inactive connections and expired cache entries."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            from backend.api.routes import get_pyrogram, get_telethon
            from backend.models.database import persistent_cache_cleanup_expired, cleanup_expired_incomplete_sessions
            
            # --- Clean expired session cache ---
            try:
                await persistent_cache_cleanup_expired()
                await cleanup_expired_incomplete_sessions()
            except Exception as e:
                logger.warning(f"[Cleanup] Cache cleanup error: {e}")
            
            # --- Clean dead connections ---
            pyrogram_mgr = get_pyrogram()
            cleaned = await pyrogram_mgr.cleanup_inactive_clients()
            
            telethon_mgr = get_telethon()
            telethon_cleaned = 0
            for phone in list(telethon_mgr.active_clients.keys()):
                client = telethon_mgr.active_clients.get(phone)
                if not client:
                    continue
                try:
                    if not client.is_connected():
                        telethon_mgr.active_clients.pop(phone, None)
                        telethon_cleaned += 1
                except:
                    try:
                        await client.disconnect()
                    except:
                        pass
                    telethon_mgr.active_clients.pop(phone, None)
                    telethon_cleaned += 1
            
            total = cleaned + telethon_cleaned
            if total > 0:
                logger.info(f"[Cleanup] Freed {total} dead connections (Pyrogram: {cleaned}, Telethon: {telethon_cleaned})")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"[Cleanup] Error: {e}")


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
    
    # Start log bot (HTTP mode - no conflicts)
    try:
        from backend.log_bot import init_log_bot
        await init_log_bot()
        logger.info("Log bot started (HTTP mode)")
    except Exception as e:
        logger.warning(f"Log bot warning: {e}")
    
    # Start periodic cleanup task (every 5 minutes)
    cleanup_task = asyncio.create_task(_periodic_cleanup())
    
    yield
    
    # Cancel cleanup task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    
    logger.info("Shutting down...")
    try:
        # Stop log bot
        from backend.log_bot import stop_log_bot
        await stop_log_bot()
        logger.info("Log bot stopped")
    except:
        pass
    
    try:
        from backend.api.routes import get_pyrogram, get_telethon
        
        pyrogram_mgr = get_pyrogram()
        if pyrogram_mgr:
            await pyrogram_mgr.disconnect_all()
            logger.info("Pyrogram sessions disconnected")
        
        telethon_mgr = get_telethon()
        if telethon_mgr:
            await telethon_mgr.disconnect_all()
            logger.info("Telethon sessions disconnected")
    except Exception as e:
        logger.warning(f"Shutdown warning: {e}")
    
    logger.info("Shutdown complete")


app = FastAPI(
    title="Escrow Account Manager",
    description="Telegram Account Management System using Pyrogram and Telethon",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS + ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

app.include_router(api_router)
app.include_router(webhook_router)
app.include_router(auth_router, prefix="/api/v2")
app.include_router(sessions_router, prefix="/api/v2")
app.include_router(admin_router, prefix="/api/v2")
app.include_router(delivery_router, prefix="/api/v2")
app.include_router(audit_router, prefix="/api/v2")

@app.get("/")
async def root():
    return FileResponse("frontend/index_main.html")

@app.get("/dashboard")
async def dashboard():
    return FileResponse("frontend/dashboard.html")

@app.get("/receive")
async def receive_page():
    return FileResponse("frontend/receive.html")

@app.get("/receive-delivery")
async def receive_delivery_page(phone: str = None):
    """Delivery page with phone parameter"""
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
