import asyncio
import sqlite3
from backend.models.database import DATABASE_URL

async def add_column():
    db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("ALTER TABLE accounts ADD COLUMN generated_password VARCHAR(255)")
        print("Column 'generated_password' added successfully!")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e):
            print("Column 'generated_password' already exists!")
        else:
            print(f"Error: {e}")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    asyncio.run(add_column())
