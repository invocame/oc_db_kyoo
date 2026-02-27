import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

logger = logging.getLogger("oc_db_kyoo")


@dataclass
class BackendStats:
    """Real-time statistics for a single backend."""
    name: str
    active_requests: int = 0
    queued_requests: int = 0
    total_requests: int = 0
    total_completed: int = 0
    total_errors: int = 0
    total_timeouts: int = 0
    total_rejected: int = 0
    avg_response_time_ms: float = 0.0
    _response_times: list = field(default_factory=list, repr=False)

    def record_response_time(self, duration_ms: float):
        """Track response time with a rolling window of last 100 requests."""
        self._response_times.append(duration_ms)
        if len(self._response_times) > 100:
            self._response_times.pop(0)
        self.avg_response_time_ms = sum(self._response_times) / len(self._response_times)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "active_requests": self.active_requests,
            "queued_requests": self.queued_requests,
            "total_requests": self.total_requests,
            "total_completed": self.total_completed,
            "total_errors": self.total_errors,
            "total_timeouts": self.total_timeouts,
            "total_rejected": self.total_rejected,
            "avg_response_time_ms": round(self.avg_response_time_ms, 2),
        }


class BackendQueue:
    """
    Manages concurrency and queuing for a single database backend.
    
    Uses asyncio.Semaphore for concurrency limiting:
    - Up to max_concurrent requests are forwarded to the backend simultaneously
    - Additional requests wait in queue (up to max_queue)
    - If queue is full, requests are rejected
    - If a request waits longer than queue_timeout, it is cancelled
    """

    def __init__(self, name: str, max_concurrent: int, max_queue: int, queue_timeout: int):
        self.name = name
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self.queue_timeout = queue_timeout

        # Semaphore controls how many requests hit the backend concurrently
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue_count = 0
        self._lock = asyncio.Lock()
        self.stats = BackendStats(name=name)

    @property
    def active_requests(self) -> int:
        return self.max_concurrent - self._semaphore._value

    @property
    def queued_requests(self) -> int:
        return self._queue_count

    @property
    def total_load(self) -> int:
        """Total load = active + queued. Used for least-queue routing."""
        return self.active_requests + self._queue_count

    def is_queue_full(self) -> bool:
        """Check if this backend can accept more requests in its queue."""
        return self._queue_count >= self.max_queue

    async def acquire(self) -> bool:
        """
        Try to acquire a slot to send a request to this backend.
        
        Returns True if acquired (either immediately or after waiting in queue).
        Returns False if the queue is full (request should be rejected).
        Raises asyncio.TimeoutError if queue_timeout is exceeded.
        """
        # Check if we can enter the queue
        async with self._lock:
            if self._queue_count >= self.max_queue and self._semaphore.locked():
                self.stats.total_rejected += 1
                return False
            self._queue_count += 1
            self.stats.queued_requests = self._queue_count
            self.stats.total_requests += 1

        try:
            # Wait for a slot with timeout
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=self.queue_timeout
            )
            # Got a slot — no longer queued, now active
            async with self._lock:
                self._queue_count -= 1
                self.stats.queued_requests = self._queue_count
                self.stats.active_requests = self.active_requests
            return True
        except asyncio.TimeoutError:
            # Timed out waiting in queue
            async with self._lock:
                self._queue_count -= 1
                self.stats.queued_requests = self._queue_count
                self.stats.total_timeouts += 1
            raise

    def release(self):
        """Release a slot after the request to the backend completes."""
        self._semaphore.release()
        self.stats.active_requests = self.active_requests

    def record_success(self, duration_ms: float):
        self.stats.total_completed += 1
        self.stats.record_response_time(duration_ms)

    def record_error(self):
        self.stats.total_errors += 1


class QueueManager:
    """
    Manages multiple backend queues and implements least-queue routing.
    
    Each backend has its own independent queue with its own concurrency limits.
    When a new request arrives, it is routed to the backend with the lowest total load.
    """

    def __init__(self, max_concurrent: int, max_queue: int, queue_timeout: int):
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self.queue_timeout = queue_timeout
        self._backends: Dict[str, BackendQueue] = {}

    def add_backend(self, name: str) -> BackendQueue:
        """Register a new backend with its own queue."""
        bq = BackendQueue(
            name=name,
            max_concurrent=self.max_concurrent,
            max_queue=self.max_queue,
            queue_timeout=self.queue_timeout,
        )
        self._backends[name] = bq
        logger.info(
            f"Backend '{name}' registered: "
            f"max_concurrent={self.max_concurrent}, "
            f"max_queue={self.max_queue}, "
            f"queue_timeout={self.queue_timeout}s"
        )
        return bq

    def get_backend(self, name: str) -> Optional[BackendQueue]:
        return self._backends.get(name)

    def select_backend(self) -> Optional[BackendQueue]:
        """
        Select the backend with the lowest total load (least-queue strategy).
        Returns None if ALL backends have full queues.
        """
        available = [
            bq for bq in self._backends.values()
            if not bq.is_queue_full() or not bq._semaphore.locked()
        ]
        if not available:
            return None
        return min(available, key=lambda bq: bq.total_load)

    def all_stats(self) -> list:
        """Return statistics for all backends."""
        return [bq.stats.to_dict() for bq in self._backends.values()]

    def is_healthy(self) -> bool:
        """Service is healthy if at least one backend can accept requests."""
        return any(
            not bq.is_queue_full() or not bq._semaphore.locked()
            for bq in self._backends.values()
        )

    @property
    def backend_names(self) -> list:
        return list(self._backends.keys())
