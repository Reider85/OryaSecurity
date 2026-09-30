"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Copy, Hash } from "lucide-react";
import { AuditEventResponse } from "@/lib/api";

interface AuditRowDetailProps {
  event: AuditEventResponse;
}

export function AuditRowDetail({ event }: AuditRowDetailProps) {
  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const formatDateTime = (dateString: string) => {
    return new Date(dateString).toLocaleString();
  };

  return (
    <Card className="mb-4">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Hash className="h-4 w-4" />
          Event Details
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Basic Info */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-sm font-medium text-muted-foreground">Request ID</label>
            <div className="flex items-center gap-2 mt-1">
              <span className="font-mono text-sm">{event.request_id}</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => copyToClipboard(event.request_id)}
                className="h-6 w-6 p-0"
              >
                <Copy className="h-3 w-3" />
              </Button>
            </div>
          </div>
          <div>
            <label className="text-sm font-medium text-muted-foreground">Timestamp</label>
            <div className="text-sm mt-1">{formatDateTime(event.ts)}</div>
          </div>
        </div>

        {/* Verdict and Reason */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="text-sm font-medium text-muted-foreground">Verdict</label>
            <div className="mt-1">
              <Badge variant={event.verdict === "allow" ? "default" : "destructive"}>
                {event.verdict.toUpperCase()}
              </Badge>
            </div>
          </div>
          <div>
            <label className="text-sm font-medium text-muted-foreground">Reason</label>
            <div className="text-sm mt-1">{event.reason || "-"}</div>
          </div>
        </div>

        {/* Latency */}
        <div>
          <label className="text-sm font-medium text-muted-foreground">Latency</label>
          <div className="text-sm mt-1">
            {event.latency_ms ? `${event.latency_ms}ms` : "-"}
          </div>
        </div>

        {/* Prompt Hash */}
        <div>
          <label className="text-sm font-medium text-muted-foreground">Prompt Hash</label>
          <div className="flex items-center gap-2 mt-1">
            <span className="font-mono text-sm">{event.prompt_hash}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => copyToClipboard(event.prompt_hash)}
              className="h-6 w-6 p-0"
            >
              <Copy className="h-3 w-3" />
            </Button>
          </div>
        </div>

        {/* Redacted Text */}
        {event.prompt_text_redacted && (
          <div>
            <label className="text-sm font-medium text-muted-foreground">Redacted Prompt</label>
            <div className="mt-1 p-3 bg-muted rounded-md text-sm">
              {event.prompt_text_redacted}
            </div>
          </div>
        )}

        {/* Rules Matched */}
        {event.rules_matched && event.rules_matched.length > 0 && (
          <div>
            <label className="text-sm font-medium text-muted-foreground">Rules Matched</label>
            <div className="mt-2 flex flex-wrap gap-2">
              {event.rules_matched.map((rule, index) => (
                <Badge key={index} variant="outline" className="text-xs">
                  {rule.rule_id}
                  {rule.position && ` (pos: ${rule.position})`}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Policy Version */}
        {event.policy_version && (
          <div>
            <label className="text-sm font-medium text-muted-foreground">Policy Version</label>
            <div className="text-sm mt-1">{event.policy_version}</div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}