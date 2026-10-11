"""
Thread-safe Circuit Breaker pattern implementation for external service protection.
Prevents cascading timeouts and resource exhaustion when third-party or local AI services fail.
"""
from __future__ import annotations

from enum import Enum
import logging
import threading
import time
from typing import Any, Callable, TypeVar

from app.exceptions import CircuitBreakerOpenError

logger = logging.getLogger("ewaste.circuit_breaker")

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"      # Normal operation, passes calls through
    OPEN = "OPEN"          # Tripped, immediately fails fast
    HALF_OPEN = "HALF_OPEN"  # Testing recovery with single probe request


class CircuitBreaker:
    """Monitors failures to an external service and fails fast when tripped."""

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker for '%s' transitioned to HALF_OPEN (probing)", self.service_name)
            return self._state

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute callable protected by the circuit breaker."""
        current_state = self.state

        if current_state == CircuitState.OPEN:
            elapsed = time.time() - self._last_failure_time
            remaining = max(0.1, round(self.recovery_timeout - elapsed, 1))
            logger.warning(
                "Circuit breaker for '%s' is OPEN. Fast-failing request (retry in %ss)",
                self.service_name,
                remaining,
            )
            raise CircuitBreakerOpenError(self.service_name, cooldown_seconds=remaining)

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            raise

    def _on_success(self) -> None:
        with self._lock:
            if self._state != CircuitState.CLOSED:
                logger.info("Service '%s' recovered successfully. Circuit breaker closed.", self.service_name)
            self._state = CircuitState.CLOSED
            self._failure_count = 0

    def _on_failure(self, exc: Exception) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            logger.warning(
                "Service '%s' recorded failure (%s/%s): %s",
                self.service_name,
                self._failure_count,
                self.failure_threshold,
                type(exc).__name__,
            )
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.error(
                    "Circuit breaker for '%s' TRIPPED to OPEN for %s seconds.",
                    self.service_name,
                    self.recovery_timeout,
                )

    def reset(self) -> None:
        """Manually reset circuit breaker state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = 0.0

    def get_status(self) -> dict[str, Any]:
        """Return live health telemetry for observability dashboard."""
        return {
            "service": self.service_name,
            "state": self.state.value,
            "failures": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "last_failure_time": self._last_failure_time,
        }


# Global circuit breakers for external AI components
tavily_circuit_breaker = CircuitBreaker("tavily", failure_threshold=3, recovery_timeout=30.0)
ollama_circuit_breaker = CircuitBreaker("ollama", failure_threshold=3, recovery_timeout=30.0)
