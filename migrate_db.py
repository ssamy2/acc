import asyncio
from backend.models.database import init_db

async def migrate():
    print("Initializing database with new schema...")
    await init_db()
    print("Database migration completed!")

if __name__ == "__main__":
    asyncio.run(migrate())
