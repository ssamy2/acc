import sqlite3
from backend.models.database import DATABASE_URL

def migrate():
    db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS incomplete_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone VARCHAR(20) NOT NULL,
                step VARCHAR(50) NOT NULL,
                pyrogram_session TEXT,
                telethon_session TEXT,
                generated_password VARCHAR(255),
                last_code VARCHAR(20),
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS ix_incomplete_sessions_phone ON incomplete_sessions (phone)")
        print("Table 'incomplete_sessions' created successfully!")
    except sqlite3.OperationalError as e:
        print(f"Error: {e}")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    migrate()
