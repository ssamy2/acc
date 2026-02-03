import sqlite3
from backend.models.database import DATABASE_URL

def migrate():
    db_path = DATABASE_URL.replace("sqlite+aiosqlite:///", "")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    columns_to_add = [
        ("delivery_status", "VARCHAR(50)"),
        ("last_code", "VARCHAR(20)"),
        ("code_sent_at", "DATETIME"),
        ("confirmation_deadline", "DATETIME"),
        ("delivered_at", "DATETIME")
    ]
    
    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE accounts ADD COLUMN {col_name} {col_type}")
            print(f"Added column: {col_name}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e):
                print(f"Column already exists: {col_name}")
            else:
                print(f"Error adding {col_name}: {e}")
    
    conn.commit()
    conn.close()
    print("Migration completed!")

if __name__ == "__main__":
    migrate()
