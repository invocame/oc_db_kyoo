import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.queue_manager import QueueManager

logger = logging.getLogger("oc_db_kyoo")

router = APIRouter()

# Will be set by app.py at startup
_queue_manager: QueueManager = None


def init_health(queue_manager: QueueManager):
    global _queue_manager
    _queue_manager = queue_manager


@router.get("/health")
async def health():
    """
    Liveness/readiness probe for Kubernetes.
    Returns 200 if the service is running and at least one backend can accept requests.
    Returns 503 if all backends are overloaded.
    """
    if _queue_manager is None:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Service not initialized"}
        )

    if _queue_manager.is_healthy():
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "backends": len(_queue_manager.backend_names)}
        )
    else:
        return JSONResponse(
            status_code=503,
            content={"status": "overloaded", "message": "All backends are busy"}
        )


@router.get("/status")
async def status():
    """
    Detailed status endpoint showing per-backend queue statistics.
    Useful for debugging and monitoring.
    """
    if _queue_manager is None:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "message": "Service not initialized"}
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok" if _queue_manager.is_healthy() else "overloaded",
            "backends": _queue_manager.all_stats(),
        }
    )
