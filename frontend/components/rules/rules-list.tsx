"use client";

import { useMemo } from "react";
import { Badge } from "@/components/ui/badge";
import type { Rule } from "@/lib/api";
import { cn } from "@/lib/utils";

const SEVERITY_VARIANT: Record<string, "secondary" | "warning" | "destructive" | "default"> = {
  low: "secondary",
  medium: "warning",
  high: "default",
  critical: "destructive",
};

interface RulesListProps {
  rules: Rule[];
  selectedId?: string | null;
  onSelect: (id: string) => void;
  loading?: boolean;
}

export function RulesList({ rules, selectedId, onSelect, loading }: RulesListProps) {
  const groups = useMemo(() => {
    const map = new Map<string, Rule[]>();
    for (const rule of rules) {
      const file = rule.source_file || "unknown";
      const list = map.get(file) ?? [];
      list.push(rule);
      map.set(file, list);
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
  }, [rules]);

  if (loading) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
        Loading rules...
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center text-sm text-muted-foreground">
        No rules found.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {groups.map(([file, fileRules]) => (
        <div key={file} className="space-y-1">
          <div className="rounded-md bg-accent px-2 py-1.5 text-sm font-medium">
            {file.replace(/\.yaml$/, "")}/
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              {fileRules.length} rules
            </span>
          </div>
          <div className="ml-2 space-y-0.5">
            {fileRules.map((rule) => (
              <button
                key={rule.id}
                type="button"
                onClick={() => onSelect(rule.id)}
                className={cn(
                  "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                  selectedId === rule.id
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:bg-accent"
                )}
              >
                <span className="truncate">{rule.id}</span>
                <span className="ml-2 flex shrink-0 items-center gap-1">
                  {!rule.enabled && (
                    <Badge variant="outline" className="text-[10px]">
                      off
                    </Badge>
                  )}
                  <Badge
                    variant={SEVERITY_VARIANT[rule.severity] ?? "secondary"}
                    className="text-[10px]"
                  >
                    {rule.severity}
                  </Badge>
                </span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
