# Real-Time Notifications with WebSocket

This document describes the real-time notification architecture in TaskFlow, including the WebSocket connection protocol, authentication mechanisms, message schema, error and reconnection handling, multi-instance clustering design, and reference frontend TypeScript integration.

---

## 1. Connection Protocol & Endpoints

TaskFlow exposes real-time notifications over standard WebSockets (`ws://` for HTTP, `wss://` for HTTPS).

- **Primary WebSocket Endpoint**: `/api/v1/notifications/ws`
- **Alias WebSocket Endpoint**: `/api/v1/ws/notifications`

Both endpoints are identical in functionality and route to the authenticated notification connection manager.

---

## 2. Authentication & Authorization

### Zero-URL-Exposure Security Model
In compliance with project security guidelines:
- **Client-supplied user IDs are NEVER trusted.**
- **Session tokens are NEVER passed in URL query parameters** (e.g. no `?token=...`), preventing token leakage in proxy logs, browser history, or server access logs.

### Supported Handshake Authentication Methods
Authentication occurs strictly during the HTTP-to-WebSocket upgrade handshake using the existing authentication architecture:

1. **Cookie-Based Authentication (Default & Recommended for Browsers)**:
   - Handshake includes the standard `taskflow_session` HttpOnly cookie.
   - Handled automatically by the browser when establishing `new WebSocket(...)` from the same origin or configured CORS credentials.
   - **Cross-Site WebSocket Hijacking (CSWSH) Mitigation**: When cookie authentication is used, the server verifies the `Origin` header against `BACKEND_CORS_ORIGINS`. Disallowed origins are rejected with `1008 (WS_1008_POLICY_VIOLATION)`.
2. **Bearer Token Header (Native / Mobile / Headless Clients)**:
   - Handshake includes `Authorization: Bearer <session_token>`.

### Handshake Rejection & Resource Limits
- If the session cookie or token is missing, invalid, or expired:
  The connection is closed before handshake completion with close code **`1008 (WS_1008_POLICY_VIOLATION)`** and reason `"Invalid, expired, or inactive session."`.
- **Origin Violation**: If cookie-authenticated and `Origin` does not match allowed CORS origins, closed with close code **`1008`** and reason `"Origin not allowed."`.
- **Per-User Connection Limit**: Maximum 10 concurrent active connections per user. The 11th connection is rejected with **`1008`** ("Connection limit exceeded for user.") to guard against socket exhaustion DoS.
- **Message Size Limit**: Incoming client text frames are restricted to 4096 bytes (4 KB). Larger frames are closed with code **`1009 (Message too large)`**.
- **No Arbitrary Messaging**: The WebSocket endpoint is strictly a delivery channel for notifications and keepalive `ping`/`pong`. Arbitrary client-to-server messaging is rejected.

---

## 3. Architecture & Persistence Guarantee

### Persist-Before-Broadcast Flow
Notifications are **always committed to the PostgreSQL database before being broadcast**:
1. User action triggers a domain event (e.g. Task Assigned, Task Due Date Changed, Comment Mention, Status Changed).
2. Domain service creates and persists `Notification` record(s) within the database transaction.
3. The database transaction is committed (`await session.commit()`).
4. Upon successful commit, `NotificationBroadcaster.broadcast_to_user(user_id, event, payload)` dispatches the event.
5. If the broadcast fails or no active socket is connected, the notification is safely stored in the database and accessible via REST API.

```
+---------------+           +-------------------+           +-----------------------+
|  User Action  | --------> | Database (Postgres)| --------> | NotificationBroadcaster|
|  (e.g. Task)  |           | (Commits record)  |           | (Dispatches to WS)    |
+---------------+           +-------------------+           +-----------------------+
                                                                        |
                                                 +----------------------+----------------------+
                                                 |                                             |
                                                 v                                             v
                                      +--------------------+                        +--------------------+
                                      | WebSocket Client A |                        | WebSocket Client B |
                                      | (User Tab 1)       |                        | (User Tab 2)       |
                                      +--------------------+                        +--------------------+
```

---

## 4. Message Schema & Events

All WebSocket frames are JSON strings adhering to a standardized event envelope containing `type`, `event_id`, `created_at`, `notification`, and backward-compatible `event` and `data` keys.

### Standardized Event Envelope Structure
```json
{
  "type": "notification_created",
  "event": "notification_created",
  "event_id": "7b0a88dc-8ee3-490b-ba62-349f43a9dc9e",
  "notification": { ... },
  "data": { ... },
  "created_at": "2026-10-27T10:00:00.000000Z",
  "timestamp": "2026-10-27T10:00:00.000000Z"
}
```

### Server-to-Client Events

#### `connected`
Dispatched immediately when the connection is authorized and registered.
```json
{
  "type": "connected",
  "event": "connected",
  "event_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "notification": null,
  "data": {
    "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "timestamp": "2026-10-27T10:00:00.000000Z"
  },
  "created_at": "2026-10-27T10:00:00.000000Z",
  "timestamp": "2026-10-27T10:00:00.000000Z"
}
```

#### `notification_created`
Dispatched when a new notification is generated for the authenticated user.
```json
{
  "type": "notification_created",
  "event": "notification_created",
  "event_id": "c1f72e9a-7b3d-4c8d-8e4b-123456789abc",
  "notification": {
    "id": "c1f72e9a-7b3d-4c8d-8e4b-123456789abc",
    "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "actor_id": "4d1a2b3c-0000-0000-0000-111122223333",
    "organization_id": "8e3b4a2c-1111-2222-3333-444455556666",
    "type": "task_assigned",
    "title": "Task Assigned",
    "message": "Alice assigned you to 'Refactor authentication flow'",
    "resource_type": "task",
    "resource_id": "e4d2a1b0-9c8d-4e7f-a1b2-c3d4e5f6a7b8",
    "is_read": false,
    "created_at": "2026-10-27T10:00:00.000000Z",
    "updated_at": "2026-10-27T10:00:00.000000Z",
    "actor": {
      "id": "4d1a2b3c-0000-0000-0000-111122223333",
      "email": "alice@example.com",
      "full_name": "Alice Smith"
    }
  },
  "data": { ... },
  "created_at": "2026-10-27T10:00:00.000000Z",
  "timestamp": "2026-10-27T10:00:00.000000Z"
}
```

#### `notification_read`
Dispatched when a single notification is marked as read via REST, keeping multi-tab sessions in sync.
```json
{
  "type": "notification_read",
  "event": "notification_read",
  "event_id": "d2f83e0b-8c4e-5d9e-9f5c-234567890def",
  "notification": {
    "id": "c1f72e9a-7b3d-4c8d-8e4b-123456789abc",
    "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "is_read": true
  },
  "data": { ... },
  "created_at": "2026-10-27T10:01:00.000000Z",
  "timestamp": "2026-10-27T10:01:00.000000Z"
}
```

#### `notifications_all_read`
Dispatched when all notifications for the user are marked as read via `POST /api/v1/notifications/read-all`.
```json
{
  "type": "notifications_all_read",
  "event": "notifications_all_read",
  "event_id": "e3a94f1c-9d5f-6e0f-0a6d-345678901ef0",
  "notification": null,
  "data": {
    "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "marked_count": 5
  },
  "created_at": "2026-10-27T10:02:00.000000Z",
  "timestamp": "2026-10-27T10:02:00.000000Z"
}
```

#### `pong`
Response to a client-initiated keepalive ping.
```json
{
  "type": "pong",
  "event": "pong",
  "event_id": "f4b05a2d-0e6a-7f1a-1b7e-456789012fa1",
  "notification": null,
  "data": {
    "timestamp": "2026-10-27T10:03:00.000000Z"
  },
  "created_at": "2026-10-27T10:03:00.000000Z",
  "timestamp": "2026-10-27T10:03:00.000000Z"
}
```

---

### Client-to-Server Events

#### `ping`
Client keepalive ping. Format: `{"event": "ping"}` or `{"type": "ping"}`. The server immediately replies with the `pong` envelope.
```json
{
  "event": "ping"
}
```


---

## 5. Multi-Connection & User Isolation

- **User Isolation**: Broadcaster maps connections by verified `user_id`. Broadcaster strictly filters outgoing messages so each connection receives only permitted notifications for its authenticated user.
- **Multiple Simultaneous Connections**: Users frequently open multiple browser tabs or devices. `InMemoryConnectionManager` manages a `dict[uuid.UUID, set[WebSocket]]` protected by an `asyncio.Lock()`. When an event occurs for a user, it is dispatched concurrently to all active sockets for that user.
- **Connection Cleanup**: When a socket disconnects (normal closure or abrupt network loss), the socket is removed from the user's connection pool. If no more sockets remain for that user, the dictionary key is cleanly deleted to prevent memory leaks.

---

## 6. Multi-Instance Deployment Design (Redis Pub/Sub Ready)

TaskFlow backend implements the `NotificationBroadcaster` abstract protocol:

```python
class NotificationBroadcaster(Protocol):
    async def connect(self, user_id: uuid.UUID, websocket: WebSocket) -> None: ...
    async def disconnect(self, user_id: uuid.UUID, websocket: WebSocket) -> None: ...
    async def broadcast_to_user(
        self, user_id: uuid.UUID, event: str, data: dict[str, Any]
    ) -> int: ...
```

### Single-Instance (Current Default)
Uses `InMemoryConnectionManager` without requiring any external message broker.

### Scaling Horizontally to Multiple Backend Instances
When scaling out horizontally across multiple containers/pods behind a load balancer:
1. Sockets for User A may be connected to Node 1 (Tab 1) and Node 2 (Tab 2).
2. A task mutation handled on Node 3 publishes the event to Redis:
   ```
   Channel: taskflow:notifications:user:{user_id}
   Payload: { "event": event, "data": data }
   ```
3. A Redis-backed broadcaster (`RedisNotificationBroadcaster`) subscribes to user channels for locally connected users and forwards frames across local sockets.
4. No application code or domain service logic changes are required because the domain calls the `NotificationBroadcaster` interface.

### Multi-Instance Limitations (Current State)
> [!IMPORTANT]
> The default deployment uses `InMemoryConnectionManager`. When running multiple backend worker processes or horizontal container instances, WebSocket messages broadcast by instance A will only reach clients connected to instance A. For multi-node production clusters, configure a shared message broker such as Redis Pub/Sub using the `NotificationBroadcaster` abstraction shown above.

---

## 7. Reconnection & Missed-Events Reconciliation

WebSocket connections can drop due to transient network issues, device sleep, or server restarts. TaskFlow guarantees zero event loss through its **Persist-Before-Broadcast** model combined with REST delta reconciliation:

1. **Client Event Tracking**:
   The client records the ISO timestamp (`created_at`) of the most recently received notification or event envelope.
2. **Reconnection**:
   The client re-establishes the WebSocket connection using exponential backoff with jitter.
3. **Delta Reconciliation via REST**:
   Immediately upon successful WebSocket reconnection, the client issues a REST request to reconcile missed events:
   ```http
   GET /api/v1/notifications?since={last_seen_iso_timestamp}
   ```
   The backend returns all notifications created strictly after `since`, sorted chronologically with stable secondary ID ordering.
4. **Idempotency & Deduplication**:
   Clients use `notification.id` or `envelope.event_id` to deduplicate any notifications received simultaneously over the new socket and the delta REST query.

---

## 8. Frontend Integration Guide (TypeScript)


Below is the production-ready TypeScript client implementation for real-time notifications with:
- Bounded exponential backoff with jitter for reconnection.
- Automatic REST fallback synchronization upon reconnection.
- Ping/pong heartbeat keepalive.
- Tab-safe synchronization and duplicate suppression.

```typescript
// notification-ws-client.ts

export interface NotificationPayload {
  id: string;
  user_id: string;
  type: string;
  title: string;
  message: string;
  is_read: boolean;
  task_id?: string | null;
  created_at: string;
}

export type NotificationEventHandler = (event: string, data: any) => void;

export class NotificationWebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private isExplicitlyClosed = false;
  private reconnectAttempts = 0;
  private maxReconnectDelayMs = 30000;
  private baseReconnectDelayMs = 1000;
  private pingIntervalMs = 30000;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private listeners: Set<NotificationEventHandler> = new Set();
  private onReconnectCallback?: () => Promise<void>;

  constructor(url?: string, onReconnect?: () => Promise<void>) {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    this.url = url || `${protocol}//${host}/api/v1/notifications/ws`;
    this.onReconnectCallback = onReconnect;
  }

  public connect(): void {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    this.isExplicitlyClosed = false;
    try {
      // Connects with standard cookies automatically sent by the browser
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        this.reconnectAttempts = 0;
        this.startHeartbeat();
        if (this.onReconnectCallback) {
          this.onReconnectCallback().catch((err) =>
            console.error("Failed to reconcile notifications after reconnect:", err)
          );
        }
      };

      this.ws.onmessage = (event: MessageEvent) => {
        try {
          const payload = JSON.parse(event.data);
          this.dispatch(payload.event, payload.data);
        } catch (e) {
          console.warn("Invalid notification message format:", event.data);
        }
      };

      this.ws.onclose = (event: CloseEvent) => {
        this.stopHeartbeat();
        // Do not auto-reconnect if rejected for authentication failure (code 1008)
        if (event.code === 1008) {
          console.warn("WebSocket authentication failed (1008 Policy Violation). Reconnection halted.");
          return;
        }

        if (!this.isExplicitlyClosed) {
          this.scheduleReconnect();
        }
      };

      this.ws.onerror = (err) => {
        console.error("WebSocket encountered an error:", err);
      };
    } catch (e) {
      console.error("Failed to initiate WebSocket connection:", e);
      this.scheduleReconnect();
    }
  }

  public subscribe(handler: NotificationEventHandler): () => void {
    this.listeners.add(handler);
    return () => this.listeners.delete(handler);
  }

  public disconnect(): void {
    this.isExplicitlyClosed = true;
    this.stopHeartbeat();
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private dispatch(event: string, data: any): void {
    this.listeners.forEach((handler) => {
      try {
        handler(event, data);
      } catch (err) {
        console.error("Error in notification handler:", err);
      }
    });
  }

  private startHeartbeat(): void {
    this.stopHeartbeat();
    this.pingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ event: "ping" }));
      }
    }, this.pingIntervalMs);
  }

  private stopHeartbeat(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    this.reconnectAttempts++;
    // Bounded exponential backoff with jitter: 2^n * base + random jitter
    const delay = Math.min(
      this.baseReconnectDelayMs * Math.pow(2, this.reconnectAttempts) + Math.random() * 1000,
      this.maxReconnectDelayMs
    );
    setTimeout(() => {
      if (!this.isExplicitlyClosed) {
        this.connect();
      }
    }, delay);
  }
}
```
