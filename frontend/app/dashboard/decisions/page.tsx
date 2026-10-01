"use client";

import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { DecisionsFilters } from "@/components/decisions/decisions-filters";
import { DecisionsTable } from "@/components/decisions/decisions-table";
import { api } from "@/lib/api";

interface DecisionsFiltersState {
  verdict?: string;
  tenant_id?: string;
  rule_id?: string;
  start_date?: string;
  end_date?: string;
}

export default function DecisionsPage() {
  const [filters, setFilters] = useState<DecisionsFiltersState>({});
  const [currentPage, setCurrentPage] = useState(1);

  // Decisions query
  const { data: decisionsData, isLoading } = useQuery({
    queryKey: ["decisions", currentPage, filters],
    queryFn: () => api.getDecisions({
      page: currentPage,
      limit: 50,
      ...filters,
    }),
    placeholderData: keepPreviousData,
  });

  const handleApplyFilters = (newFilters: DecisionsFiltersState) => {
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
        <h1 className="text-2xl font-bold">Decision Explorer</h1>
      </div>

      {/* Filters */}
      <DecisionsFilters
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
              <p className="mt-2 text-sm text-muted-foreground">Loading decisions...</p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {!isLoading && decisionsData && decisionsData.items.length === 0 && (
        <Card>
          <CardContent className="flex items-center justify-center h-32">
            <div className="text-center">
              <p className="text-muted-foreground">No decisions found.</p>
              <p className="text-sm text-muted-foreground mt-1">
                Try adjusting your filters or check back later.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Decisions Table */}
      {!isLoading && decisionsData && decisionsData.items.length > 0 && (
        <DecisionsTable
          data={decisionsData.items}
          total={decisionsData.total}
          page={decisionsData.page}
          perPage={decisionsData.per_page}
          onPageChange={handlePageChange}
        />
      )}
    </div>
  );
}