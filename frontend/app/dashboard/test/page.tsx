"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";

export default function TestPage() {
  const [prompt, setPrompt] = useState("");
  const [result, setResult] = useState<{
    verdict: string;
    reason: string;
    latency_ms: number;
    request_id: string;
    cache_hit: boolean;
  } | null>(null);
  const [loading, setLoading] = useState(false);

  const handleScan = async () => {
    if (!prompt.trim()) return;
    setLoading(true);
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/scan`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${localStorage.getItem("scanner_token") || ""}`,
          },
          body: JSON.stringify({ prompt }),
        }
      );
      const data = await res.json();
      setResult(data);
    } catch {
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Test Scanner</h1>

      <Card>
        <CardHeader>
          <CardTitle>Enter Prompt</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Textarea
            placeholder="Type a prompt to test... (e.g., 'My SSN is 123-45-6789')"
            className="min-h-[160px]"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                handleScan();
              }
            }}
          />
          <Button onClick={handleScan} disabled={loading || !prompt.trim()}>
            {loading ? "Scanning..." : "Scan (Ctrl+Enter)"}
          </Button>
        </CardContent>
      </Card>

      {result && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              Result
              <Badge variant={result.verdict === "allow" ? "success" : "destructive"}>
                {result.verdict.toUpperCase()}
              </Badge>
              <Badge variant={result.cache_hit ? "default" : "secondary"}>
                {result.cache_hit ? "HIT" : "MISS"}
              </Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-muted-foreground">Reason: </span>
                {result.reason}
              </div>
              <div>
                <span className="text-muted-foreground">Latency: </span>
                {result.latency_ms.toFixed(1)}ms
              </div>
              <div>
                <span className="text-muted-foreground">Request ID: </span>
                <code className="text-xs">{result.request_id}</code>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
