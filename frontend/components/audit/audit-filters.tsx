"use client";

import { useState } from "react";
import { format } from "date-fns";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/select";
import { DateRangePicker } from "@/components/date-range-picker";

interface AuditFiltersProps {
  onApply: (filters: {
    verdict?: string;
    prompt_hash?: string;
    start_ts?: string;
    end_ts?: string;
  }) => void;
  onReset: () => void;
  onExport: () => void;
}

export function AuditFilters({ onApply, onReset, onExport }: AuditFiltersProps) {
  const [verdict, setVerdict] = useState<string>("");
  const [promptHash, setPromptHash] = useState<string>("");
  const [startTs, setStartTs] = useState<string>("");
  const [endTs, setEndTs] = useState<string>("");

  const handleApply = () => {
    const filters = {
      verdict: verdict || undefined,
      prompt_hash: promptHash || undefined,
      start_ts: startTs || undefined,
      end_ts: endTs || undefined,
    };
    onApply(filters);
  };

  const handleReset = () => {
    setVerdict("");
    setPromptHash("");
    setStartTs("");
    setEndTs("");
    onReset();
  };

  const verdictOptions = [
    { value: "", label: "All" },
    { value: "allow", label: "Allow" },
    { value: "block", label: "Block" },
  ];

  return (
    <div className="sticky top-4 z-10 bg-background border-b p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Filters</h2>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onExport}>
            Export CSV
          </Button>
          <Button variant="ghost" onClick={handleReset}>
            Reset
          </Button>
          <Button onClick={handleApply}>Apply</Button>
        </div>
      </div>
      
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <div className="space-y-2">
          <label className="text-sm font-medium">Verdict</label>
          <Select 
            options={verdictOptions}
            value={verdict}
            onChange={(e) => setVerdict(e.target.value)}
            placeholder="Select verdict"
          />
        </div>
        
        <div className="space-y-2">
          <label className="text-sm font-medium">Prompt Hash</label>
          <Input
            placeholder="Search by prompt hash..."
            value={promptHash}
            onChange={(e) => setPromptHash(e.target.value)}
          />
        </div>
        
        <div className="space-y-2">
          <label className="text-sm font-medium">Date Range</label>
          <DateRangePicker
            start={startTs ? new Date(startTs) : undefined}
            end={endTs ? new Date(endTs) : undefined}
            onStartChange={(date) => setStartTs(date ? format(date, "yyyy-MM-dd") : "")}
            onEndChange={(date) => setEndTs(date ? format(date, "yyyy-MM-dd") : "")}
          />
        </div>
      </div>
    </div>
  );
}