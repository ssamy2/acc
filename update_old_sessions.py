import asyncio
from sqlalchemy import update
from backend.models.database import Account, engine, async_session

async def update_old_sessions():
    async with async_session() as session:
        stmt = update(Account).where(Account.generated_password == None).values(generated_password=None)
        await session.execute(stmt)
        await session.commit()
        print("Old sessions updated: generated_password set to None for accounts without password")

if __name__ == "__main__":
    asyncio.run(update_old_sessions())
