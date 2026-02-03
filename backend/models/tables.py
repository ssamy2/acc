from datetime import datetime
import enum
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Enum, LargeBinary
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class AccountStatus(enum.Enum):
    INITIALIZING = "INITIALIZING"
    PENDING_CLEANUP = "PENDING_CLEANUP"
    SECURED = "SECURED"

class LibraryType(enum.Enum):
    TDLib = "TDLib"
    Pyrogram = "Pyrogram"
    Telethon = "Telethon"

class Account(Base):
    __tablename__ = "accounts"

    tg_user_id = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String, unique=True, nullable=False, index=True)
    current_status = Column(Enum(AccountStatus), default=AccountStatus.INITIALIZING, nullable=False)
    bot_2fa_password = Column(LargeBinary, nullable=True)  # Encrypted
    version = Column(Integer, default=1, nullable=False)  # Optimistic Locking
    created_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("Session", back_populates="account", cascade="all, delete-orphan")

class Session(Base):
    __tablename__ = "sessions"

    session_id = Column(String, primary_key=True)  # UUID
    account_id = Column(Integer, ForeignKey("accounts.tg_user_id"), nullable=False)
    library_type = Column(Enum(LibraryType), nullable=False)
    encrypted_payload = Column(LargeBinary, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    account = relationship("Account", back_populates="sessions")

# Database Connection Helper
# For Production: "postgresql+asyncpg://user:password@localhost/escrow_db"
# For Dev/Demo: SQLite
DATABASE_URL = "sqlite+aiosqlite:///./escrow_system.db"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
