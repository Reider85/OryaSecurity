"use client";

import { format } from "date-fns";
import { Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { CacheEntry } from "@/lib/api";

interface CacheTableProps {
  entries: CacheEntry[];
  search: string;
  onSearchChange: (value: string) => void;
  onDelete: (hash: string) => void;
  deletingHash?: string | null;
  isLoading?: boolean;
}

function truncateHash(hash: string): string {
  return hash.length > 16 ? `${hash.slice(0, 12)}...` : hash;
}

export function relativeExpiry(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "--";
  const diff = date.getTime() - Date.now();
  const abs = Math.abs(diff);
  const minutes = Math.round(abs / 60000);
  let unit: string;
  if (minutes < 1) {
    const seconds = Math.round(abs / 1000);
    unit = `${seconds} sec`;
  } else if (minutes < 60) {
    unit = `${minutes} min`;
  } else {
    const hours = Math.round(minutes / 60);
    unit = `${hours} hour${hours === 1 ? "" : "s"}`;
  }
  return diff >= 0 ? `in ${unit}` : `${unit} ago`;
}

export function CacheTable({
  entries,
  search,
  onSearchChange,
  onDelete,
  deletingHash,
  isLoading,
}: CacheTableProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Recent Entries</CardTitle>
        <Input
          placeholder="Search by hash..."
          className="max-w-xs"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
        />
      </CardHeader>
      <CardContent>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Prompt Hash</TableHead>
                <TableHead>Verdict</TableHead>
                <TableHead>Created</TableHead>
                <TableHead>Expires</TableHead>
                <TableHead className="w-12"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                    Loading cache entries...
                  </TableCell>
                </TableRow>
              ) : entries.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="h-24 text-center text-muted-foreground">
                    No cache entries.
                  </TableCell>
                </TableRow>
              ) : (
                entries.map((entry) => (
                  <TableRow key={entry.prompt_hash}>
                    <TableCell className="font-mono text-sm" title={entry.prompt_hash}>
                      {truncateHash(entry.prompt_hash)}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={entry.verdict === "allow" ? "default" : "destructive"}
                      >
                        {entry.verdict.toUpperCase()}
                      </Badge>
                    </TableCell>
                    <TableCell>{format(new Date(entry.ts), "MMM d, HH:mm:ss")}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {relativeExpiry(entry.expires_at)}
                    </TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Delete entry"
                        disabled={deletingHash === entry.prompt_hash}
                        onClick={() => onDelete(entry.prompt_hash)}
                      >
                        <Trash2 className="h-4 w-4 text-destructive" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
