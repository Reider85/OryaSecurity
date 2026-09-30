"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import type { RuleMatch } from "@/lib/api";

const SEVERITY_VARIANT: Record<string, "secondary" | "warning" | "destructive" | "default"> = {
  low: "secondary",
  medium: "warning",
  high: "default",
  critical: "destructive",
};

interface TestModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  ruleId?: string | null;
  onRun: (text: string) => Promise<RuleMatch[]>;
}

export function TestModal({ open, onOpenChange, ruleId, onRun }: TestModalProps) {
  const [text, setText] = useState("");
  const [running, setRunning] = useState(false);
  const [matches, setMatches] = useState<RuleMatch[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleOpenChange = (value: boolean) => {
    onOpenChange(value);
    if (!value) {
      setMatches(null);
      setError(null);
    }
  };

  const handleRun = async () => {
    if (!text.trim()) return;
    setRunning(true);
    setError(null);
    try {
      const result = await onRun(text);
      setMatches(result);
    } catch (e) {
      setMatches(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Test Rule</DialogTitle>
          <DialogDescription>
            {ruleId
              ? `Scanning text against rule "${ruleId}" (saved rules only).`
              : "Scanning text against all saved rules."}
          </DialogDescription>
        </DialogHeader>

        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Enter text to scan, e.g. My SSN is 123-45-6789"
          className="min-h-[120px] font-mono text-sm"
        />

        <div className="flex gap-2">
          <Button onClick={handleRun} disabled={running || !text.trim()}>
            {running ? "Running..." : "Run"}
          </Button>
        </div>

        {error && (
          <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        {matches !== null && (
          <div className="space-y-2">
            <p className="text-sm font-medium">
              {matches.length === 0
                ? "No matches found."
                : `${matches.length} match${matches.length === 1 ? "" : "es"} found:`}
            </p>
            {matches.length > 0 && (
              <div className="max-h-64 overflow-y-auto rounded-md border">
                <table className="w-full text-sm">
                  <thead className="bg-accent text-left">
                    <tr>
                      <th className="px-3 py-2 font-medium">Rule</th>
                      <th className="px-3 py-2 font-medium">Severity</th>
                      <th className="px-3 py-2 font-medium">Action</th>
                      <th className="px-3 py-2 font-medium">Position</th>
                      <th className="px-3 py-2 font-medium">Value hash</th>
                    </tr>
                  </thead>
                  <tbody>
                    {matches.map((m, i) => (
                      <tr key={`${m.rule_id}-${i}`} className="border-t">
                        <td className="px-3 py-1.5 font-mono">{m.rule_id}</td>
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
                        <td className="px-3 py-1.5 font-mono text-xs">
                          {m.value_hash.slice(0, 8)}…
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
