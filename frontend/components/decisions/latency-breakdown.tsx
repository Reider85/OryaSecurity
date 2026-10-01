"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Clock, Zap, Shield, FileText } from "lucide-react";

interface LatencyBreakdownProps {
  breakdown: {
    fast_path_ms: number;
    slow_path_ms: number;
    pdp_ms: number;
    audit_ms: number;
    total_ms: number;
  };
}

export function LatencyBreakdown({ breakdown }: LatencyBreakdownProps) {
  const total = breakdown.total_ms;
  const data = [
    {
      name: "Fast Path",
      value: breakdown.fast_path_ms,
      color: "bg-blue-500",
      icon: Zap,
      description: "Cache lookup + basic validation"
    },
    {
      name: "Slow Path",
      value: breakdown.slow_path_ms,
      color: "bg-green-500",
      icon: Clock,
      description: "Full scan + PDP evaluation"
    },
    {
      name: "PDP",
      value: breakdown.pdp_ms,
      color: "bg-yellow-500",
      icon: Shield,
      description: "Policy Decision Point"
    },
    {
      name: "Audit",
      value: breakdown.audit_ms,
      color: "bg-purple-500",
      icon: FileText,
      description: "Audit logging"
    }
  ];

  const getPerformanceLevel = (totalMs: number) => {
    if (totalMs < 10) return { level: "Excellent", color: "text-green-600" };
    if (totalMs < 50) return { level: "Good", color: "text-blue-600" };
    if (totalMs < 100) return { level: "Fair", color: "text-yellow-600" };
    return { level: "Poor", color: "text-red-600" };
  };

  const performance = getPerformanceLevel(total);

  return (
    <div className="space-y-6">
      {/* Summary */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="text-sm font-medium text-muted-foreground">Total Latency</label>
          <div className={`text-2xl font-bold ${performance.color}`}>
            {total.toFixed(1)}ms
          </div>
          <div className="text-sm text-muted-foreground">
            Performance: {performance.level}
          </div>
        </div>
        <div>
          <label className="text-sm font-medium text-muted-foreground">Breakdown</label>
          <div className="text-sm text-muted-foreground space-y-1">
            <div>Fast: {breakdown.fast_path_ms.toFixed(1)}ms</div>
            <div>Slow: {breakdown.slow_path_ms.toFixed(1)}ms</div>
            <div>PDP: {breakdown.pdp_ms.toFixed(1)}ms</div>
            <div>Audit: {breakdown.audit_ms.toFixed(1)}ms</div>
          </div>
        </div>
      </div>

      {/* Visual Breakdown */}
      <div className="space-y-4">
        <label className="text-sm font-medium text-muted-foreground">Time Distribution</label>
        
        {data.map((item, index) => {
          const percentage = total > 0 ? (item.value / total) * 100 : 0;
          const Icon = item.icon;
          
          return (
            <div key={index} className="space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4" />
                  <span className="text-sm font-medium">{item.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm text-muted-foreground">
                    {item.value.toFixed(1)}ms ({percentage.toFixed(1)}%)
                  </span>
                </div>
              </div>
              <Progress value={percentage} className="h-2" />
            </div>
          );
        })}
      </div>

      {/* Performance Insights */}
      <div className="bg-muted rounded-lg p-4">
        <label className="text-sm font-medium text-muted-foreground mb-2">Performance Insights</label>
        <div className="text-sm space-y-1">
          {total < 10 && (
            <div className="text-green-600">
              ✅ Excellent performance! Response time is optimal.
            </div>
          )}
          {total >= 10 && total < 50 && (
            <div className="text-blue-600">
              ✅ Good performance. Response time is acceptable.
            </div>
          )}
          {total >= 50 && total < 100 && (
            <div className="text-yellow-600">
              ⚠️ Fair performance. Consider optimizing slow path operations.
            </div>
          )}
          {total >= 100 && (
            <div className="text-red-600">
              ⚠️ Poor performance. Response time needs optimization.
            </div>
          )}
          
          {breakdown.fast_path_ms > breakdown.slow_path_ms && (
            <div className="text-blue-600">
              📊 Cache efficiency is good - fast path dominates processing time.
            </div>
          )}
          
          {breakdown.audit_ms > 10 && (
            <div className="text-yellow-600">
              📝 Audit logging is taking significant time. Consider async logging.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}