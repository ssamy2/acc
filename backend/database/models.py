import sqlite3
import json
from typing import Dict, Any, Optional

DB_PATH = "escrow_system.db"

class DatabaseManager:
    """
    Manages SQLite database for Accounts and Sessions.
    """
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        
        # Enable WAL mode for concurrency
        cur.execute("PRAGMA journal_mode=WAL;")

        # Accounts Table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT UNIQUE NOT NULL,
                is_clean BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Sessions Table (Stores Encrypted Payloads)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                library TEXT NOT NULL,
                session_data TEXT,
                api_id INTEGER,
                device_hash TEXT,
                FOREIGN KEY(account_id) REFERENCES accounts(id)
            )
        """)
        
        conn.commit()
        conn.close()

    def add_account(self, phone: str) -> int:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        try:
            cur.execute("INSERT OR IGNORE INTO accounts (phone) VALUES (?)", (phone,))
            conn.commit()
            
            # Fetch ID
            cur.execute("SELECT id FROM accounts WHERE phone=?", (phone,))
            row = cur.fetchone()
            return row[0] if row else -1
        finally:
            conn.close()

    def save_session(self, account_id: int, library: str, session_data: str):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO sessions (account_id, library, session_data) 
            VALUES (?, ?, ?)
        """, (account_id, library, session_data))
        conn.commit()
        conn.close()
