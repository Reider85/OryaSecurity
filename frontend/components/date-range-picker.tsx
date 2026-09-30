"use client";

import { useState } from "react";
import { format } from "date-fns";

interface DateRangePickerProps {
  start?: Date;
  end?: Date;
  onStartChange?: (date: Date | undefined) => void;
  onEndChange?: (date: Date | undefined) => void;
}

export function DateRangePicker({ start, end, onStartChange, onEndChange }: DateRangePickerProps) {
  const [localStart, setLocalStart] = useState<Date | undefined>(start);
  const [localEnd, setLocalEnd] = useState<Date | undefined>(end);

  const handleStartChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const date = e.target.value ? new Date(e.target.value) : undefined;
    setLocalStart(date);
    onStartChange?.(date);
  };

  const handleEndChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const date = e.target.value ? new Date(e.target.value) : undefined;
    setLocalEnd(date);
    onEndChange?.(date);
  };

  return (
    <div className="flex gap-2">
      <div className="flex flex-col">
        <label className="text-sm font-medium mb-1">From</label>
        <input
          type="date"
          value={localStart ? format(localStart, "yyyy-MM-dd") : ""}
          onChange={handleStartChange}
          className="px-3 py-2 border border-input rounded-md text-sm"
        />
      </div>
      <div className="flex flex-col">
        <label className="text-sm font-medium mb-1">To</label>
        <input
          type="date"
          value={localEnd ? format(localEnd, "yyyy-MM-dd") : ""}
          onChange={handleEndChange}
          className="px-3 py-2 border border-input rounded-md text-sm"
        />
      </div>
    </div>
  );
}