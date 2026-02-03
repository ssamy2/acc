import asyncio
import logging
import uuid
import time
from typing import Optional
import redis.asyncio as redis

logger = logging.getLogger("Redlock")

class MockRedisLockManager:
    """Mock Redis Lock Manager for testing without Redis server"""
    def __init__(self, redis_url: str = "redis://localhost"):
        self.locks = {}
        logger.info("Using MockRedisLockManager (Redis not required)")
    
    async def acquire_lock(self, resource: str, ttl_ms: int = 10000) -> Optional[str]:
        token = str(uuid.uuid4())
        if resource not in self.locks:
            self.locks[resource] = token
            return token
        return None
    
    async def release_lock(self, resource: str, token: str):
        if self.locks.get(resource) == token:
            del self.locks[resource]
    
    async def close(self):
        pass

class RedisLockManager:
    def __init__(self, redis_url: str = "redis://localhost"):
        self.redis = redis.from_url(redis_url, decode_responses=True)

    async def acquire_lock(self, resource: str, ttl_ms: int = 10000) -> Optional[str]:
        """
        Tries to acquire a lock on `resource`.
        Returns the lock_value (UUID) if successful, None otherwise.
        """
        token = str(uuid.uuid4())
        # NX: Set only if not exists
        # PX: Expiry in milliseconds
        is_set = await self.redis.set(resource, token, nx=True, px=ttl_ms)
        if is_set:
            return token
        return None

    async def release_lock(self, resource: str, token: str):
        """
        Releases the lock only if the token matches (Lua script).
        """
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        try:
            await self.redis.eval(lua_script, 1, resource, token)
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")

    async def close(self):
        await self.redis.close()
