"""
تشغيل Escrow Account Manager V2
"""
import sys
import os

# إضافة المسار الحالي
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Run migration before starting server
print("=" * 60)
print("   Starting Escrow Account Manager V2")
print("   Pyrogram + Telethon")
print("=" * 60)
print()

try:
    from migrate_all_columns import add_missing_columns
    print("🔄 Running database migration...")
    add_missing_columns()
except Exception as e:
    print(f"⚠️  Migration warning: {e}")

print()
print("Server: http://localhost:8001")
print("API Docs: http://localhost:8001/docs")
print()
print("=" * 60)

import uvicorn

if __name__ == "__main__":
    
    uvicorn.run(
        "backend.main_v2:app",
        host="0.0.0.0",
        port=8001,
        reload=True
    )
