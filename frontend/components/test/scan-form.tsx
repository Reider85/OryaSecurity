"use client";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

interface ScanFormProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  loading: boolean;
}

export function ScanForm({ value, onChange, onSubmit, loading }: ScanFormProps) {
  return (
    <div className="space-y-4">
      <Textarea
        placeholder="Type a prompt to test... (e.g., 'My SSN is 123-45-6789')"
        rows={10}
        className="min-h-[240px] font-mono text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault();
            onSubmit();
          }
        }}
      />
      <Button onClick={onSubmit} disabled={loading || !value.trim()}>
        {loading ? "Scanning..." : "Scan (Ctrl+Enter)"}
      </Button>
    </div>
  );
}
