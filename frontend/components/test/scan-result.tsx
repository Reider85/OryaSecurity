"use client";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Copy } from "lucide-react";
import type { ScanResult } from "@/lib/api";
import { TextHighlighter } from "@/components/test/text-highlighter";

const SEVERITY_VARIANT: Record<string, "secondary" | "warning" | "destructive" | "default"> = {
  low: "secondary",
  medium: "warning",
  high: "default",
  critical: "destructive",
};

interface ScanResultCardProps {
  result: ScanResult;
  originalPrompt: string;
}

export function ScanResultCard({ result, originalPrompt }: ScanResultCardProps) {
  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Result
          <Badge variant={result.verdict === "allow" ? "success" : "destructive"}>
            {result.verdict === "allow" ? "Allow" : "Block"}
          </Badge>
          <Badge variant={result.cache_hit ? "default" : "secondary"}>
            {result.cache_hit ? "HIT" : "MISS"}
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <span className="text-muted-foreground">Reason: </span>
            {result.reason}
          </div>
          <div>
            <span className="text-muted-foreground">Latency: </span>
            {result.latency_ms.toFixed(1)}ms
          </div>
          <div className="sm:col-span-2">
            <span className="text-muted-foreground">Request ID: </span>
            <code className="text-xs">{result.request_id}</code>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => copyToClipboard(result.request_id)}
              className="ml-1 h-6 w-6 p-0"
              aria-label="Copy request ID"
            >
              <Copy className="h-3 w-3" />
            </Button>
          </div>
        </div>

        <div>
          <label className="text-sm font-medium text-muted-foreground">
            Highlighted matches
          </label>
          <div className="mt-1">
            <TextHighlighter text={originalPrompt} matches={result.rules_matched} />
          </div>
        </div>

        <div>
          <label className="text-sm font-medium text-muted-foreground">
            Rules matched ({result.rules_matched.length})
          </label>
          {result.rules_matched.length === 0 ? (
            <p className="mt-1 text-sm text-muted-foreground">No rules matched.</p>
          ) : (
            <div className="mt-2 overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-accent text-left">
                  <tr>
                    <th className="px-3 py-2 font-medium">Rule</th>
                    <th className="px-3 py-2 font-medium">Severity</th>
                    <th className="px-3 py-2 font-medium">Action</th>
                    <th className="px-3 py-2 font-medium">Position</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rules_matched.map((m, i) => (
                    <tr key={`${m.rule_id}-${i}`} className="border-t">
                      <td className="px-3 py-1.5 font-mono">{m.rule_name || m.rule_id}</td>
                      <td className="px-3 py-1.5">
                        <Badge
                          variant={SEVERITY_VARIANT[m.severity] ?? "secondary"}
                          className="text-[10px]"
                        >
                          {m.severity}
                        </Badge>
                      </td>
                      <td className="px-3 py-1.5">{m.action}</td>
                      <td className="px-3 py-1.5 font-mono text-xs">
                        [{m.position[0]}, {m.position[1]}]
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
