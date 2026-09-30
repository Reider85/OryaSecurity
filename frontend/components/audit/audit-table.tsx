"use client";

import { useState } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  ColumnDef,
  SortingState,
  ColumnFiltersState,
} from "@tanstack/react-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ChevronDown, ChevronRight } from "lucide-react";
import { AuditEventResponse } from "@/lib/api";
import { AuditRowDetail } from "./audit-row-detail";

interface AuditTableProps {
  data: AuditEventResponse[];
  total: number;
  page: number;
  perPage: number;
  onPageChange: (page: number) => void;
}

export function AuditTable({ data, total, page, perPage, onPageChange }: AuditTableProps) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [expandedRowId, setExpandedRowId] = useState<number | null>(null);

  const columns: ColumnDef<AuditEventResponse>[] = [
    {
      accessorKey: "ts",
      header: "Time",
      cell: ({ getValue }) => {
        const date = new Date(getValue() as string);
        return date.toLocaleString();
      },
      enableSorting: true,
    },
    {
      accessorKey: "request_id",
      header: "Request ID",
      cell: ({ getValue }) => {
        const id = getValue() as string;
        return `${id.slice(0, 8)}...`;
      },
    },
    {
      accessorKey: "prompt_hash",
      header: "Prompt Hash",
      cell: ({ getValue }) => {
        const hash = getValue() as string;
        return `${hash.slice(0, 12)}...`;
      },
    },
    {
      accessorKey: "verdict",
      header: "Verdict",
      cell: ({ getValue }) => {
        const verdict = getValue() as string;
        return (
          <Badge variant={verdict === "allow" ? "default" : "destructive"}>
            {verdict.toUpperCase()}
          </Badge>
        );
      },
    },
    {
      accessorKey: "reason",
      header: "Reason",
      cell: ({ getValue }) => getValue() as string || "-",
    },
    {
      accessorKey: "latency_ms",
      header: "Latency (ms)",
      cell: ({ getValue }) => {
        const latency = getValue() as number;
        return latency ? `${latency}ms` : "-";
      },
    },
    {
      id: "expand",
      header: "",
      cell: ({ row }) => (
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setExpandedRowId(expandedRowId === row.original.id ? null : row.original.id)}
        >
          {expandedRowId === row.original.id ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </Button>
      ),
    },
  ];

  const table = useReactTable({
    data,
    columns,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getSortedRowModel: getSortedRowModel(),
    onSortingChange: setSorting,
    onColumnFiltersChange: setColumnFilters,
    state: {
      sorting,
      columnFilters,
    },
  });

  const totalPages = Math.ceil(total / perPage);

  return (
    <div className="space-y-4">
      <div className="rounded-md border">
        <table className="w-full">
          <thead>
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="h-12 px-4 text-left align-middle font-medium text-muted-foreground"
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <>
                <tr
                  key={row.id}
                  className="border-b hover:bg-muted/50 cursor-pointer"
                  onClick={() => setExpandedRowId(expandedRowId === row.original.id ? null : row.original.id)}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="p-2 align-middle">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
                {expandedRowId === row.original.id && (
                  <tr>
                    <td colSpan={columns.length} className="p-0">
                      <AuditRowDetail event={row.original} />
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between">
        <div className="text-sm text-muted-foreground">
          Showing {((page - 1) * perPage) + 1} to {Math.min(page * perPage, total)} of {total} results
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onPageChange(page - 1)}
            disabled={page === 1}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages}
          >
            Next
          </Button>
        </div>
      </div>
    </div>
  );
}

// Helper function for flex rendering
function flexRender<T>(
  cell: React.ReactNode | ((props: any) => React.ReactNode),
  context: any
): React.ReactNode {
  if (typeof cell === "function") {
    return cell(context);
  }
  return cell;
}