"""
Configuration file for development and production environments
"""
import os
import platform

# Detect environment
IS_WINDOWS = platform.system() == "Windows"
DEV_MODE = os.getenv("DEV_MODE", "true" if IS_WINDOWS else "false").lower() == "true"

# API Configuration
API_ID = 28907635
API_HASH = "fa6c3335de68283781976ae20f813f73"

# URLs based on environment
if DEV_MODE:
    # Development (Windows/Local)
    API_BASE_URL = "http://127.0.0.1:8001"
    FRONTEND_URL = "http://127.0.0.1:8001"
    ALLOWED_ORIGINS = [
        "http://localhost:8001",
        "http://127.0.0.1:8001",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
else:
    # Production (Linux/Ubuntu)
    API_BASE_URL = "https://acctest.channelsseller.site"
    FRONTEND_URL = "https://acctest.channelsseller.site"
    ALLOWED_ORIGINS = [
        "https://acctest.channelsseller.site",
        "http://acctest.channelsseller.site",
    ]

# Email domain
EMAIL_DOMAIN = "channelsseller.site"

# Session timeout (30 minutes)
SESSION_TIMEOUT_SECONDS = 30 * 60

print(f"🔧 Config loaded: DEV_MODE={DEV_MODE}, Platform={platform.system()}")
print(f"📡 API URL: {API_BASE_URL}")
print(f"🌐 Frontend URL: {FRONTEND_URL}")
