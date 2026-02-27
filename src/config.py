import os
import json
import logging
from typing import List, Optional
from pydantic import BaseModel, field_validator

logger = logging.getLogger("oc_db_kyoo")


class BackendConfig(BaseModel):
    name: str
    host: str
    port: int
    path: str = "/"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.path}"


class AppConfig(BaseModel):
    listen_port: int = 8080
    log_level: str = "info"
    backends: List[BackendConfig]
    max_concurrent_per_backend: int = 10
    max_queue_per_backend: int = 50
    queue_timeout: int = 120
    backend_timeout: int = 900

    @field_validator("backends")
    @classmethod
    def check_backends_not_empty(cls, v):
        if not v:
            raise ValueError("At least one backend must be configured")
        return v

    @field_validator("max_concurrent_per_backend", "max_queue_per_backend")
    @classmethod
    def check_positive(cls, v):
        if v < 1:
            raise ValueError("Value must be at least 1")
        return v

    @field_validator("queue_timeout", "backend_timeout")
    @classmethod
    def check_timeout_positive(cls, v):
        if v < 1:
            raise ValueError("Timeout must be at least 1 second")
        return v


def load_config(config_path: str = "conf.json") -> AppConfig:
    """
    Load configuration from conf.json, then override with Docker ENV variables.
    Follows the same pattern as oc_api: conf.json is the base, ENV takes precedence.
    """

    # Load conf.json
    with open(config_path) as f:
        c = json.load(f)

    # ENV overrides (Docker / Kubernetes)
    listen_port = int(os.getenv("LISTEN_PORT", c.get("listen_port", 8080)))
    log_level = os.getenv("LOG_LEVEL", c.get("log_level", "info"))
    max_concurrent = int(os.getenv("MAX_CONCURRENT_PER_BACKEND", c.get("max_concurrent_per_backend", 10)))
    max_queue = int(os.getenv("MAX_QUEUE_PER_BACKEND", c.get("max_queue_per_backend", 50)))
    queue_timeout = int(os.getenv("QUEUE_TIMEOUT", c.get("queue_timeout", 120)))
    backend_timeout = int(os.getenv("BACKEND_TIMEOUT", c.get("backend_timeout", 900)))

    # Backends configuration priority:
    # 1. Individual env vars: BACKEND_0_HOST, BACKEND_1_HOST, etc. (Kubernetes-friendly)
    # 2. Fall back to conf.json "backends" list for any missing values
    #
    # Discovery: scan BACKEND_N_HOST env vars to find how many backends are defined.
    # If no env vars are found, use conf.json backends as-is.

    backends_from_conf = c.get("backends", [])

    # Discover backends from env vars (BACKEND_0_HOST, BACKEND_1_HOST, ...)
    env_backend_count = 0
    while os.getenv(f"BACKEND_{env_backend_count}_HOST"):
        env_backend_count += 1

    # Determine how many backends we have
    num_backends = max(env_backend_count, len(backends_from_conf))

    backends = []
    for i in range(num_backends):
        # Get conf.json defaults for this index (if available)
        conf_defaults = backends_from_conf[i] if i < len(backends_from_conf) else {}

        backend = BackendConfig(
            name=os.getenv(f"BACKEND_{i}_NAME", conf_defaults.get("name", f"backend-{i}")),
            host=os.getenv(f"BACKEND_{i}_HOST", conf_defaults.get("host", "localhost")),
            port=int(os.getenv(f"BACKEND_{i}_PORT", conf_defaults.get("port", 8890))),
            path=os.getenv(f"BACKEND_{i}_PATH", conf_defaults.get("path", "/")),
        )
        backends.append(backend)

    config = AppConfig(
        listen_port=listen_port,
        log_level=log_level,
        backends=backends,
        max_concurrent_per_backend=max_concurrent,
        max_queue_per_backend=max_queue,
        queue_timeout=queue_timeout,
        backend_timeout=backend_timeout,
    )

    logger.info(f"Configuration loaded: {len(config.backends)} backends")
    for b in config.backends:
        logger.info(f"  Backend '{b.name}': {b.url}")
    logger.info(f"  Max concurrent per backend: {config.max_concurrent_per_backend}")
    logger.info(f"  Max queue per backend: {config.max_queue_per_backend}")
    logger.info(f"  Queue timeout: {config.queue_timeout}s")
    logger.info(f"  Backend timeout: {config.backend_timeout}s")

    return config
