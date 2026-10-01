"use client";

import { useState } from "react";
import { format } from "date-fns";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/select";
import { DateRangePicker } from "@/components/date-range-picker";

interface DecisionsFiltersProps {
  onApply: (filters: {
    verdict?: string;
    tenant_id?: string;
    rule_id?: string;
    start_date?: string;
    end_date?: string;
  }) => void;
  onReset: () => void;
  onExport: () => void;
}

export function DecisionsFilters({ onApply, onReset, onExport }: DecisionsFiltersProps) {
  const [verdict, setVerdict] = useState<string>("");
  const [tenantId, setTenantId] = useState<string>("");
  const [ruleId, setRuleId] = useState<string>("");
  const [startDate, setStartDate] = useState<string>("");
  const [endDate, setEndDate] = useState<string>("");

  const handleApply = () => {
    const filters = {
      verdict: verdict || undefined,
      tenant_id: tenantId || undefined,
      rule_id: ruleId || undefined,
      start_date: startDate || undefined,
      end_date: endDate || undefined,
    };
    onApply(filters);
  };

  const handleReset = () => {
    setVerdict("");
    setTenantId("");
    setRuleId("");
    setStartDate("");
    setEndDate("");
    onReset();
  };

  const verdictOptions = [
    { value: "", label: "All" },
    { value: "allow", label: "Allow" },
    { value: "block", label: "Block" },
  ];

  const ruleOptions = [
    { value: "", label: "All Rules" },
    { value: "pii_ssn_us", label: "US SSN" },
    { value: "pii_passport_ru", label: "RU Passport" },
    { value: "pii_email", label: "Email" },
    { value: "secret_aws_key", label: "AWS Key" },
    { value: "secret_jwt", label: "JWT" },
    { value: "secret_credit_card", label: "Credit Card" },
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
      
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
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
          <label className="text-sm font-medium">Tenant ID</label>
          <Input
            placeholder="Enter tenant ID..."
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
          />
        </div>
        
        <div className="space-y-2">
          <label className="text-sm font-medium">Rule ID</label>
          <Select 
            options={ruleOptions}
            value={ruleId}
            onChange={(e) => setRuleId(e.target.value)}
            placeholder="Select rule"
          />
        </div>
        
        <div className="space-y-2">
          <label className="text-sm font-medium">Start Date</label>
          <Input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </div>
        
        <div className="space-y-2">
          <label className="text-sm font-medium">End Date</label>
          <Input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
          />
        </div>
      </div>
    </div>
  );
}