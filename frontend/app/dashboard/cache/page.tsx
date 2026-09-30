"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CacheStatsCards } from "@/components/cache/cache-stats";
import { CacheTable } from "@/components/cache/cache-table";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function CachePage() {
  const { token } = useAuth();
  const queryClient = useQueryClient();
  const authToken = token ?? undefined;

  const [search, setSearch] = useState("");
  const [flushOpen, setFlushOpen] = useState(false);

  const statsQuery = useQuery({
    queryKey: ["cache-stats"],
    queryFn: () => api.getCacheStats(authToken),
  });

  const entriesQuery = useQuery({
    queryKey: ["cache-entries"],
    queryFn: () => api.getCacheEntries(50, authToken),
  });

  const invalidateCacheQueries = () => {
    queryClient.invalidateQueries({ queryKey: ["cache-entries"] });
    queryClient.invalidateQueries({ queryKey: ["cache-stats"] });
  };

  const deleteMutation = useMutation({
    mutationFn: (hash: string) => api.deleteCacheEntry(hash, authToken),
    onSuccess: invalidateCacheQueries,
  });

  const flushMutation = useMutation({
    mutationFn: () => api.flushCache(authToken),
    onSuccess: () => {
      setFlushOpen(false);
      invalidateCacheQueries();
    },
  });

  const entries = useMemo(() => {
    const all = entriesQuery.data ?? [];
    const q = search.trim().toLowerCase();
    if (!q) return all;
    return all.filter((e) => e.prompt_hash.toLowerCase().includes(q));
  }, [entriesQuery.data, search]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Cache Management</h1>
        <Button variant="destructive" onClick={() => setFlushOpen(true)}>
          Flush All
        </Button>
      </div>

      {(deleteMutation.isError || flushMutation.isError) && (
        <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {deleteMutation.isError
            ? `Delete failed: ${deleteMutation.error instanceof Error ? deleteMutation.error.message : String(deleteMutation.error)}`
            : `Flush failed: ${flushMutation.error instanceof Error ? flushMutation.error.message : String(flushMutation.error)}`}
        </div>
      )}

      <CacheStatsCards stats={statsQuery.data} isLoading={statsQuery.isLoading} />

      <CacheTable
        entries={entries}
        search={search}
        onSearchChange={setSearch}
        onDelete={(hash) => deleteMutation.mutate(hash)}
        deletingHash={deleteMutation.isPending ? deleteMutation.variables : null}
        isLoading={entriesQuery.isLoading}
      />

      <Dialog open={flushOpen} onOpenChange={setFlushOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Flush all cache entries?</DialogTitle>
            <DialogDescription>
              This will delete every cached decision. Subsequent scans will re-run
              through the full pipeline until new entries are written.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFlushOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={flushMutation.isPending}
              onClick={() => flushMutation.mutate()}
            >
              {flushMutation.isPending ? "Flushing..." : "Flush All"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
