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
2. **Bearer Token Header (Native / Mobile / Headless Clients)**:
   - Handshake includes `Authorization: Bearer <session_token>`.

### Handshake Rejection
If the session cookie or token is missing, invalid, or expired:
- The WebSocket connection is immediately closed before handshake completion with close code **`1008 (WS_1008_POLICY_VIOLATION)`** and reason `"Unauthorized"`.
- The client receives an authentication failure event and should not attempt infinite reconnects without refreshing the session.

---

## 3. Architecture & Persistence Guarantee

### Persist-Before-Broadcast Flow
Notifications are **always committed to the PostgreSQL database before being broadcast**:
1. User action triggers an event (e.g. Task Assigned, Comment Mention, Status Changed).
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

All WebSocket frames are JSON strings adhering to `{ "event": string, "data": object }`.

### Server-to-Client Events

#### `connected`
Dispatched immediately when the connection is authorized and registered.
```json
{
  "event": "connected",
  "data": {
    "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "timestamp": "2026-10-20T13:30:00.000000Z"
  }
}
```

#### `notification_created`
Dispatched when a new notification is generated for the authenticated user.
```json
{
  "event": "notification_created",
  "data": {
    "id": "c1f72e9a-7b3d-4c8d-8e4b-123456789abc",
    "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "type": "task_assigned",
    "title": "Task Assigned",
    "message": "Alice assigned you to 'Refactor authentication flow'",
    "is_read": false,
    "task_id": "e4d2a1b0-9c8d-4e7f-a1b2-c3d4e5f6a7b8",
    "created_at": "2026-10-20T13:30:00.000000Z"
  }
}
```

#### `notification_read`
Dispatched when a single notification is marked as read via REST, keeping multi-tab sessions in sync.
```json
{
  "event": "notification_read",
  "data": {
    "id": "c1f72e9a-7b3d-4c8d-8e4b-123456789abc",
    "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "is_read": true
  }
}
```

#### `notifications_all_read`
Dispatched when all notifications for the user are marked as read via `POST /api/v1/notifications/read-all`.
```json
{
  "event": "notifications_all_read",
  "data": {
    "user_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
  }
}
```

#### `pong`
Response to a client-initiated keepalive ping.
```json
{
  "event": "pong"
}
```

---

### Client-to-Server Events

#### `ping`
Client keepalive ping. The server immediately replies with `{"event": "pong"}`.
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

---

## 7. Frontend Integration Guide (TypeScript)

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
