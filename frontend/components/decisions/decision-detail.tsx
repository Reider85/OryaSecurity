"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Copy, Hash, Clock, Zap } from "lucide-react";
import { DecisionDetail } from "@/lib/api";
import { LatencyBreakdown } from "./latency-breakdown";

interface DecisionDetailProps {
  decision: DecisionDetail;
}

export function DecisionDetail({ decision }: DecisionDetailProps) {
  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const formatDateTime = (dateString: string) => {
    return new Date(dateString).toLocaleString();
  };

  return (
    <div className="space-y-4">
      {/* Basic Info Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Hash className="h-4 w-4" />
            Decision Details
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Request Info */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium text-muted-foreground">Request ID</label>
              <div className="flex items-center gap-2 mt-1">
                <span className="font-mono text-sm">{decision.request_id}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => copyToClipboard(decision.request_id)}
                  className="h-6 w-6 p-0"
                >
                  <Copy className="h-3 w-3" />
                </Button>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium text-muted-foreground">Timestamp</label>
              <div className="text-sm mt-1">{formatDateTime(decision.created_at)}</div>
            </div>
          </div>

          {/* Tenant ID */}
          <div>
            <label className="text-sm font-medium text-muted-foreground">Tenant ID</label>
            <div className="text-sm mt-1">{decision.tenant_id || "-"}</div>
          </div>

          {/* Verdict and Reason */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium text-muted-foreground">Verdict</label>
              <div className="mt-1">
                <Badge variant={decision.verdict === "allow" ? "default" : "destructive"}>
                  {decision.verdict.toUpperCase()}
                </Badge>
              </div>
            </div>
            <div>
              <label className="text-sm font-medium text-muted-foreground">Cache Status</label>
              <div className="mt-1">
                <Badge variant={decision.cache_status === "HIT" ? "default" : "secondary"}>
                  {decision.cache_status}
                </Badge>
              </div>
            </div>
          </div>

          {/* Reason */}
          <div>
            <label className="text-sm font-medium text-muted-foreground">Reason</label>
            <div className="text-sm mt-1">{decision.reason || "-"}</div>
          </div>

          {/* Policy Version */}
          <div>
            <label className="text-sm font-medium text-muted-foreground">Policy Version</label>
            <div className="text-sm mt-1">{decision.policy_version}</div>
          </div>
        </CardContent>
      </Card>

      {/* Latency Breakdown Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock className="h-4 w-4" />
            Latency Breakdown
          </CardTitle>
        </CardHeader>
        <CardContent>
          <LatencyBreakdown breakdown={decision.latency_breakdown} />
        </CardContent>
      </Card>

      {/* Prompt Text Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Zap className="h-4 w-4" />
            Redacted Prompt
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="mb-4">
            <label className="text-sm font-medium text-muted-foreground">Prompt Hash</label>
            <div className="flex items-center gap-2 mt-1">
              <span className="font-mono text-sm">{decision.prompt_hash}</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => copyToClipboard(decision.prompt_hash)}
                className="h-6 w-6 p-0"
              >
                <Copy className="h-3 w-3" />
              </Button>
            </div>
          </div>
          
          <div className="bg-muted rounded-md p-4">
            <div className="text-sm whitespace-pre-wrap">{decision.prompt_text_redacted}</div>
          </div>

          {/* Rules Matched with Highlighting */}
          {decision.rules_matched && decision.rules_matched.length > 0 && (
            <div className="mt-4">
              <label className="text-sm font-medium text-muted-foreground">Rules Matched</label>
              <div className="mt-2 space-y-2">
                {decision.rules_matched.map((rule, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <Badge variant="outline" className="text-xs">
                      {rule.rule_name}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {rule.severity} • {rule.action}
                    </span>
                    {rule.position && (
                      <span className="text-xs text-muted-foreground">
                        (pos: {rule.position.start}-{rule.position.end})
                      </span>
                    )}
                    {rule.matched_value && (
                      <Badge variant="secondary" className="text-xs">
                        {rule.matched_value}
                      </Badge>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}