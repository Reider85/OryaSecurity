"use client";

import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { AuditFilters } from "@/components/audit/audit-filters";
import { AuditTable } from "@/components/audit/audit-table";
import { api } from "@/lib/api";

interface AuditFiltersState {
  verdict?: string;
  prompt_hash?: string;
  start_ts?: string;
  end_ts?: string;
}

export default function AuditPage() {
  const [filters, setFilters] = useState<AuditFiltersState>({});
  const [currentPage, setCurrentPage] = useState(1);

  // Audit events query
  const { data: auditData, isLoading } = useQuery({
    queryKey: ["audit-events", currentPage, filters],
    queryFn: () => api.getAuditEvents({
      page: currentPage,
      limit: 50,
      ...filters,
    }),
    placeholderData: keepPreviousData,
  });

  const handleApplyFilters = (newFilters: AuditFiltersState) => {
    setFilters(newFilters);
    setCurrentPage(1); // Reset to first page when filters change
  };

  const handleResetFilters = () => {
    setFilters({});
    setCurrentPage(1);
  };

  const handleExportCSV = () => {
    // Placeholder for CSV export functionality
    console.log("Exporting CSV with filters:", filters);
    alert("CSV export functionality would be implemented here");
  };

  const handlePageChange = (newPage: number) => {
    setCurrentPage(newPage);
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Audit Log</h1>
      </div>

      {/* Filters */}
      <AuditFilters
        onApply={handleApplyFilters}
        onReset={handleResetFilters}
        onExport={handleExportCSV}
      />

      {/* Loading state */}
      {isLoading && (
        <Card>
          <CardContent className="flex items-center justify-center h-32">
            <div className="text-center">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto"></div>
              <p className="mt-2 text-sm text-muted-foreground">Loading audit events...</p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {!isLoading && auditData && auditData.items.length === 0 && (
        <Card>
          <CardContent className="flex items-center justify-center h-32">
            <div className="text-center">
              <p className="text-muted-foreground">No audit events found.</p>
              <p className="text-sm text-muted-foreground mt-1">
                Try adjusting your filters or check back later.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Audit Table */}
      {!isLoading && auditData && auditData.items.length > 0 && (
        <AuditTable
          data={auditData.items}
          total={auditData.total}
          page={auditData.page}
          perPage={auditData.per_page}
          onPageChange={handlePageChange}
        />
      )}
    </div>
  );
}
