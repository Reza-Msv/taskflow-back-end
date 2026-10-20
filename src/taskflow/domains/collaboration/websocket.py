import asyncio
import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class NotificationBroadcaster(Protocol):
    """Protocol defining interface for dispatching real-time notifications."""

    async def connect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        """Register active WebSocket for a user."""
        ...

    async def disconnect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        """Unregister active WebSocket for a user."""
        ...

    async def broadcast_to_user(
        self,
        user_id: uuid.UUID,
        event: str,
        payload: dict[str, Any],
    ) -> int:
        """Broadcast message to all active WebSockets belonging to user."""
        ...

    async def broadcast_to_users(
        self,
        user_ids: Iterable[uuid.UUID],
        event: str,
        payload: dict[str, Any],
    ) -> int:
        """Broadcast message to multiple users."""
        ...


class InMemoryConnectionManager:
    """
    Manages active WebSocket connections grouped by user_id.

    Thread-safe and async-safe connection tracking with support for
    multiple tabs/devices per user and automatic disconnect cleanup.
    Designed so that a Redis Pub/Sub adapter can easily wrap or replace it
    in multi-node deployments.
    """

    def __init__(self) -> None:
        # Maps user_id -> set of active WebSocket instances
        self._connections: dict[uuid.UUID, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        """Accept handshake and record connection under user_id."""
        await websocket.accept()
        async with self._lock:
            if user_id not in self._connections:
                self._connections[user_id] = set()
            self._connections[user_id].add(websocket)
        logger.debug(
            "WebSocket connected for user %s (total for user: %d)",
            user_id,
            len(self._connections.get(user_id, set())),
        )

    async def disconnect(self, user_id: uuid.UUID, websocket: WebSocket) -> None:
        """Remove WebSocket instance from active user connections."""
        async with self._lock:
            if user_id in self._connections:
                self._connections[user_id].discard(websocket)
                if not self._connections[user_id]:
                    del self._connections[user_id]
        logger.debug("WebSocket disconnected for user %s", user_id)

    async def broadcast_to_user(
        self,
        user_id: uuid.UUID,
        event: str,
        payload: dict[str, Any],
    ) -> int:
        """
        Send JSON payload to all active WebSockets for given user.
        Automatically cleans up any broken or disconnected sockets.
        Returns the number of successful deliveries.
        """
        async with self._lock:
            sockets = list(self._connections.get(user_id, set()))

        if not sockets:
            return 0

        message = {
            "event": event,
            "data": payload,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        sent_count = 0
        dead_sockets: list[WebSocket] = []

        for ws in sockets:
            try:
                await ws.send_json(message)
                sent_count += 1
            except Exception as exc:
                logger.debug(
                    "Failed to deliver WebSocket message to socket for user %s: %s",
                    user_id,
                    exc,
                )
                dead_sockets.append(ws)

        if dead_sockets:
            async with self._lock:
                if user_id in self._connections:
                    for ws in dead_sockets:
                        self._connections[user_id].discard(ws)
                    if not self._connections[user_id]:
                        del self._connections[user_id]

        return sent_count

    async def broadcast_to_users(
        self,
        user_ids: Iterable[uuid.UUID],
        event: str,
        payload: dict[str, Any],
    ) -> int:
        """Broadcast payload to multiple distinct users."""
        total_delivered = 0
        for uid in set(user_ids):
            total_delivered += await self.broadcast_to_user(uid, event, payload)
        return total_delivered

    def is_user_connected(self, user_id: uuid.UUID) -> bool:
        """Check if user has at least one active connection."""
        return bool(self._connections.get(user_id))

    def get_user_connection_count(self, user_id: uuid.UUID) -> int:
        """Return count of active connections for specific user."""
        return len(self._connections.get(user_id, set()))

    def get_total_connections_count(self) -> int:
        """Return total active WebSocket connections across all users."""
        return sum(len(s) for s in self._connections.values())

    async def reset(self) -> None:
        """Clear all active connections (for testing teardown)."""
        async with self._lock:
            self._connections.clear()


# Global singleton instance for single-process runtime
notification_broadcaster = InMemoryConnectionManager()


def get_notification_broadcaster() -> InMemoryConnectionManager:
    """Dependency provider returning singleton broadcaster."""
    return notification_broadcaster
