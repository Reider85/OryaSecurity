"use client";

import { Card, CardContent } from "@/components/ui/card";
import type { CacheStats } from "@/lib/api";

interface CacheStatsCardsProps {
  stats?: CacheStats;
  isLoading?: boolean;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTtl(seconds: number): string {
  if (seconds <= 0) return "--";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export function CacheStatsCards({ stats, isLoading }: CacheStatsCardsProps) {
  if (isLoading || !stats) {
    return (
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardContent className="flex h-24 items-center justify-center">
            <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-primary"></div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
      <StatCard title="Total Entries" value={String(stats.total_entries)} />
      <StatCard
        title="Hit Rate (24h)"
        value={`${stats.hit_rate_24h.toFixed(1)}%`}
      />
      <StatCard title="Memory Usage" value={formatBytes(stats.memory_usage)} />
      <StatCard title="TTL Average" value={formatTtl(stats.ttl_average)} />
    </div>
  );
}

function StatCard({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-xl border bg-card p-6">
      <p className="text-sm font-medium text-muted-foreground">{title}</p>
      <p className="mt-2 text-3xl font-bold">{value}</p>
    </div>
  );
}
