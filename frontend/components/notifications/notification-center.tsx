"use client";

import { useState } from "react";
import { Bell, CheckCheck, Wifi, WifiOff } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import {
  useNotifications,
  PostFactumNotification,
} from "@/lib/use-notifications";

const SEVERITY_VARIANT: Record<
  string,
  "secondary" | "warning" | "destructive" | "default"
> = {
  low: "secondary",
  medium: "warning",
  high: "warning",
  critical: "destructive",
};

const SOURCE_LABEL: Record<string, string> = {
  rules: "Rules",
  toxicity: "Toxicity",
  vector_search: "Vector match",
  ml_classifier: "ML classifier",
};

function FindingSummary({ notification }: { notification: PostFactumNotification }) {
  const sources = notification.findings.map(
    (f) => SOURCE_LABEL[f.source] ?? f.source
  );
  return (
    <span className="text-xs text-muted-foreground">
      {sources.join(", ") || "post-factum"}
    </span>
  );
}

export function NotificationCenter() {
  const { token } = useAuth();
  const { notifications, unread, connected, markAllRead, clear } =
    useNotifications(token);
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="icon"
        title={
          connected
            ? "Post-factum notifications (live)"
            : "Post-factum notifications (disconnected)"
        }
        onClick={() => {
          setOpen((prev) => !prev);
          if (!open) markAllRead();
        }}
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium text-destructive-foreground">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </Button>

      {open && (
        <>
          <div
            className="fixed inset-0 z-40"
            onClick={() => setOpen(false)}
            aria-hidden
          />
          <div className="absolute right-0 z-50 mt-2 w-96 rounded-md border bg-popover shadow-md">
            <div className="flex items-center justify-between border-b px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">Post-factum alerts</span>
                {connected ? (
                  <Wifi className="h-3 w-3 text-green-500" />
                ) : (
                  <WifiOff className="h-3 w-3 text-muted-foreground" />
                )}
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={markAllRead}
                >
                  <CheckCheck className="mr-1 h-3 w-3" />
                  Read all
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={clear}
                  disabled={notifications.length === 0}
                >
                  Clear
                </Button>
              </div>
            </div>

            <div className="max-h-96 overflow-y-auto">
              {notifications.length === 0 ? (
                <div className="px-3 py-6 text-center text-sm text-muted-foreground">
                  No post-factum flags yet
                </div>
              ) : (
                notifications.map((n, index) => (
                  <div
                    key={`${n.request_id}-${index}`}
                    className="border-b px-3 py-2 last:border-b-0"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <Badge
                        variant={SEVERITY_VARIANT[n.severity] ?? "secondary"}
                        className="text-[10px] uppercase"
                      >
                        {n.severity ?? "high"}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {new Date(n.ts).toLocaleTimeString()}
                      </span>
                    </div>
                    <div className="mt-1 text-xs">
                      <FindingSummary notification={n} />
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                      {n.request_id.slice(0, 8)}… · {n.prompt_hash.slice(0, 12)}…
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
