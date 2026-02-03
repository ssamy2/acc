"""
Auto-migration script to add all missing columns to the database
Runs automatically on startup
"""
import sqlite3
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "./")))

DB_PATH = "escrow_accounts.db"

# All columns that should exist in the accounts table
REQUIRED_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "phone": "VARCHAR(20) UNIQUE NOT NULL",
    "telegram_id": "INTEGER",
    "transfer_mode": "VARCHAR(20) DEFAULT 'bot_only'",
    "email_hash": "VARCHAR(50)",
    "target_email": "VARCHAR(255)",
    "email_changed": "BOOLEAN DEFAULT 0",
    "email_verified": "BOOLEAN DEFAULT 0",
    "delivery_count": "INTEGER DEFAULT 0",
    "pyrogram_healthy": "BOOLEAN DEFAULT 1",
    "telethon_healthy": "BOOLEAN DEFAULT 1",
    "has_delete_request": "BOOLEAN DEFAULT 0",
    "pyrogram_session": "TEXT",
    "telethon_session": "TEXT",
    "generated_password": "VARCHAR(255)",
    "first_name": "VARCHAR(255)",
    "status": "VARCHAR(50)",
    "code_sent_at": "DATETIME",
    "last_code": "VARCHAR(10)",
    "confirmation_deadline": "DATETIME",
    "has_2fa": "BOOLEAN DEFAULT 0",
    "other_sessions_count": "INTEGER DEFAULT 0",
    "audit_passed": "BOOLEAN DEFAULT 0",
    "audit_issues": "TEXT",
    "created_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
    "updated_at": "DATETIME DEFAULT CURRENT_TIMESTAMP",
    "completed_at": "DATETIME",
    "delivered_at": "DATETIME",
    "has_recovery_email": "BOOLEAN DEFAULT 0"
}


def get_existing_columns(cursor, table_name="accounts"):
    """Get list of existing columns in table"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return {row[1]: row[2] for row in cursor.fetchall()}


def add_missing_columns(db_path=DB_PATH):
    """Add all missing columns to the accounts table"""
    if not os.path.exists(db_path):
        print(f"Database {db_path} does not exist yet. Will be created on first run.")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Get existing columns
        existing = get_existing_columns(cursor)
        print(f"Found {len(existing)} existing columns")
        
        # Add missing columns
        added_count = 0
        for col_name, col_type in REQUIRED_COLUMNS.items():
            if col_name not in existing:
                try:
                    # Extract just the type and default for ALTER TABLE
                    type_parts = col_type.split()
                    if "PRIMARY KEY" in col_type or "AUTOINCREMENT" in col_type:
                        # Skip primary key columns - they must exist
                        continue
                    
                    alter_sql = f"ALTER TABLE accounts ADD COLUMN {col_name} {col_type}"
                    cursor.execute(alter_sql)
                    print(f"✓ Added column: {col_name}")
                    added_count += 1
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        print(f"✗ Error adding {col_name}: {e}")
        
        conn.commit()
        
        if added_count > 0:
            print(f"\n✅ Successfully added {added_count} missing columns")
        else:
            print("\n✅ All columns already exist")
        
        # Verify
        final_columns = get_existing_columns(cursor)
        print(f"\nFinal column count: {len(final_columns)}")
        
    except Exception as e:
        print(f"❌ Migration error: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Database Migration - Adding Missing Columns")
    print("=" * 60)
    add_missing_columns()
    print("=" * 60)
