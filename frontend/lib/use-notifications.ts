"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface PostFactumFinding {
  source: string;
  severity: string;
  details: Record<string, unknown>;
}

export interface PostFactumNotification {
  type: "post_factum_flag";
  request_id: string;
  tenant_id: string | null;
  severity: string;
  prompt_hash: string;
  findings: PostFactumFinding[];
  latency_ms: number;
  ts: string;
}

const MAX_NOTIFICATIONS = 20;
const BASE_RECONNECT_DELAY_MS = 1000;
const MAX_RECONNECT_DELAY_MS = 30000;
// Server rejection close codes — never reconnect on these.
const STOP_RECONNECT_CODES = [4401, 4403];

function buildWsUrl(token: string): string {
  const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const url = new URL(apiBase);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/v2/ws/notifications";
  url.search = new URLSearchParams({ api_key: token }).toString();
  return url.toString();
}

/**
 * Live SecOps notifications over WebSocket.
 *
 * Connects to /v2/ws/notifications, buffers the last 20 post-factum flags
 * and tracks an unread counter. Reconnects with exponential backoff
 * (1s → 30s), stopping permanently on auth rejection (4401/4403).
 */
export function useNotifications(token: string | null) {
  const [notifications, setNotifications] = useState<PostFactumNotification[]>([]);
  const [connected, setConnected] = useState(false);
  const [unread, setUnread] = useState(0);
  const attemptRef = useRef(0);

  const markAllRead = useCallback(() => setUnread(0), []);

  const clear = useCallback(() => {
    setNotifications([]);
    setUnread(0);
  }, []);

  useEffect(() => {
    if (!token) return;

    let ws: WebSocket | null = null;
    let stopped = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

    const scheduleReconnect = () => {
      if (stopped) return;
      const delay = Math.min(
        BASE_RECONNECT_DELAY_MS * 2 ** attemptRef.current,
        MAX_RECONNECT_DELAY_MS
      );
      attemptRef.current += 1;
      reconnectTimer = setTimeout(connect, delay);
    };

    const connect = () => {
      if (stopped) return;
      try {
        ws = new WebSocket(buildWsUrl(token));
      } catch {
        scheduleReconnect();
        return;
      }

      ws.onopen = () => {
        attemptRef.current = 0;
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as PostFactumNotification;
          if (message?.type === "post_factum_flag") {
            setNotifications((prev) => [message, ...prev].slice(0, MAX_NOTIFICATIONS));
            setUnread((n) => n + 1);
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      ws.onerror = () => {
        ws?.close();
      };

      ws.onclose = (event) => {
        setConnected(false);
        if (stopped) return;
        if (STOP_RECONNECT_CODES.includes(event.code)) return;
        scheduleReconnect();
      };
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [token]);

  return { notifications, unread, connected, markAllRead, clear };
}
