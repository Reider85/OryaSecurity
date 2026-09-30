"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScanForm } from "@/components/test/scan-form";
import { ScanResultCard } from "@/components/test/scan-result";
import { api, type ScanResult } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function TestPage() {
  const { token } = useAuth();
  const authToken = token ?? undefined;

  const [prompt, setPrompt] = useState("");
  const [result, setResult] = useState<ScanResult | null>(null);
  const [submittedPrompt, setSubmittedPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const scanMutation = useMutation({
    mutationFn: (text: string) => api.scanPrompt(text, authToken),
    onSuccess: (data, text) => {
      setResult(data);
      setSubmittedPrompt(text);
      setError(null);
    },
    onError: (e) => {
      setResult(null);
      setError(e instanceof Error ? e.message : String(e));
    },
  });

  const handleScan = () => {
    if (!prompt.trim()) return;
    scanMutation.mutate(prompt);
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Test Scanner</h1>

      <Card>
        <CardHeader>
          <CardTitle>Enter Prompt</CardTitle>
        </CardHeader>
        <CardContent>
          <ScanForm
            value={prompt}
            onChange={setPrompt}
            onSubmit={handleScan}
            loading={scanMutation.isPending}
          />
        </CardContent>
      </Card>

      {error && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {result && <ScanResultCard result={result} originalPrompt={submittedPrompt} />}
    </div>
  );
}
