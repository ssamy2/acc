import os
from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, Enum as SQLEnum
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
import enum

# Get absolute path to database
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, "escrow_accounts.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

print(f"📁 Database path: {DB_PATH}")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

Base = declarative_base()


class AuthStatus(enum.Enum):
    PENDING_CODE = "pending_code"
    PENDING_2FA = "pending_2fa"
    AUTHENTICATED = "authenticated"
    AUDIT_PENDING = "audit_pending"
    AUDIT_FAILED = "audit_failed"
    AUDIT_PASSED = "audit_passed"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"  # Session timed out (30 min limit)


class DeliveryStatus(enum.Enum):
    BOT_RECEIVED = "bot_received"  # Bot received account from seller (after finalize)
    READY = "ready"  # Ready for buyer to receive
    WAITING_CODE = "waiting_code"
    CODE_SENT = "code_sent"
    BUYER_DELIVERED = "buyer_delivered"  # Delivered to buyer via /receive
    DELIVERED = "delivered"  # Legacy - same as buyer_delivered
    FORCE_SECURED = "force_secured"
    EXPIRED = "expired"


class TransferMode(enum.Enum):
    BOT_ONLY = "bot_only"  # User exits, only bot sessions remain
    USER_KEEPS_SESSION = "user_keeps_session"  # User keeps one session


class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    telegram_id = Column(Integer, nullable=True)
    first_name = Column(String(100), nullable=True)
    
    status = Column(SQLEnum(AuthStatus), default=AuthStatus.PENDING_CODE)
    
    pyrogram_session = Column(Text, nullable=True)
    telethon_session = Column(Text, nullable=True)
    
    has_2fa = Column(Boolean, default=False)
    has_recovery_email = Column(Boolean, default=False)
    other_sessions_count = Column(Integer, default=0)
    
    generated_password = Column(String(255), nullable=True)
    
    delivery_status = Column(SQLEnum(DeliveryStatus), nullable=True)
    last_code = Column(String(20), nullable=True)
    code_sent_at = Column(DateTime, nullable=True)
    confirmation_deadline = Column(DateTime, nullable=True)
    
    # Transfer mode and settings
    transfer_mode = Column(SQLEnum(TransferMode), default=TransferMode.BOT_ONLY)
    
    # Recovery email management
    email_hash = Column(String(50), nullable=True, index=True)  # Encrypted hash for email
    target_email = Column(String(255), nullable=True)  # Our email for this account
    email_changed = Column(Boolean, default=False)  # Whether email was changed to ours
    email_verified = Column(Boolean, default=False)  # Whether we verified the email change
    
    # Delivery tracking
    delivery_count = Column(Integer, default=0)  # How many times delivered (1, 2, 3...)
    
    # Session health
    pyrogram_healthy = Column(Boolean, default=True)
    telethon_healthy = Column(Boolean, default=True)
    last_session_check = Column(DateTime, nullable=True)
    
    # Delete request tracking
    has_delete_request = Column(Boolean, default=False)
    
    audit_passed = Column(Boolean, nullable=True)
    audit_issues = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    delivered_at = Column(DateTime, nullable=True)


class AuthLog(Base):
    __tablename__ = "auth_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(20), nullable=False, index=True)
    action = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)
    details = Column(Text, nullable=True)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class IncompleteSession(Base):
    __tablename__ = "incomplete_sessions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(20), nullable=False, index=True)
    step = Column(String(50), nullable=False)
    pyrogram_session = Column(Text, nullable=True)
    telethon_session = Column(Text, nullable=True)
    generated_password = Column(String(255), nullable=True)
    last_code = Column(String(20), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def add_account(phone: str) -> Account:
    async with async_session() as session:
        account = Account(phone=phone)
        session.add(account)
        await session.commit()
        await session.refresh(account)
        return account


async def get_account(phone: str) -> Optional[Account]:
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(Account).where(Account.phone == phone)
        )
        return result.scalar_one_or_none()


async def update_account(phone: str, **kwargs) -> Optional[Account]:
    async with async_session() as session:
        from sqlalchemy import select, update
        
        await session.execute(
            update(Account).where(Account.phone == phone).values(**kwargs)
        )
        await session.commit()
        
        result = await session.execute(
            select(Account).where(Account.phone == phone)
        )
        return result.scalar_one_or_none()


async def log_auth_action(phone: str, action: str, status: str, details: str = None, ip: str = None):
    async with async_session() as session:
        log = AuthLog(
            phone=phone,
            action=action,
            status=status,
            details=details,
            ip_address=ip
        )
        session.add(log)
        await session.commit()


async def save_incomplete_session(
    phone: str, 
    step: str, 
    pyrogram_session: str = None,
    telethon_session: str = None,
    generated_password: str = None,
    last_code: str = None,
    error_message: str = None
):
    from datetime import timedelta
    async with async_session() as session:
        from sqlalchemy import delete
        await session.execute(delete(IncompleteSession).where(IncompleteSession.phone == phone))
        
        incomplete = IncompleteSession(
            phone=phone,
            step=step,
            pyrogram_session=pyrogram_session,
            telethon_session=telethon_session,
            generated_password=generated_password,
            last_code=last_code,
            error_message=error_message,
            expires_at=datetime.utcnow() + timedelta(hours=24)
        )
        session.add(incomplete)
        await session.commit()


async def get_incomplete_session(phone: str) -> Optional[IncompleteSession]:
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(IncompleteSession).where(IncompleteSession.phone == phone)
        )
        return result.scalar_one_or_none()


async def delete_incomplete_session(phone: str):
    async with async_session() as session:
        from sqlalchemy import delete
        await session.execute(delete(IncompleteSession).where(IncompleteSession.phone == phone))
        await session.commit()


async def cleanup_expired_incomplete_sessions():
    async with async_session() as session:
        from sqlalchemy import delete
        await session.execute(
            delete(IncompleteSession).where(IncompleteSession.expires_at < datetime.utcnow())
        )
        await session.commit()


async def get_all_incomplete_sessions():
    async with async_session() as session:
        from sqlalchemy import select
        result = await session.execute(select(IncompleteSession))
        return result.scalars().all()
