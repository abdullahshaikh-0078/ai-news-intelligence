from typing import AsyncGenerator, Tuple, Optional
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from app.core.config import settings
from app.core.logging import logger

# Create centralized async engine with robust pooling and Neon auto-suspend resilience
engine: AsyncEngine = create_async_engine(
    settings.async_database_url,
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,  # Proactively test connections before checkout (resilient to Neon auto-suspend)
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session with automatic lifecycle management."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def ping_database(session: Optional[AsyncSession] = None) -> Tuple[bool, Optional[str]]:
    """Verify active database connectivity.

    Returns:
        (is_healthy, error_message_if_any)
    """
    try:
        if session:
            await session.execute(text("SELECT 1"))
        else:
            async with AsyncSessionLocal() as temp_session:
                await temp_session.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        logger.warning(f"Database health ping failed: {exc.__class__.__name__}: {exc}")
        return False, str(exc.__class__.__name__)
