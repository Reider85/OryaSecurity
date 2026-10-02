"use client"

import { useQuery } from "@tanstack/react-query"
import { Shield, AlertTriangle, Clock, Database } from "lucide-react"
import { KpiCard } from "@/components/metrics/kpi-card"
import { RequestsChart } from "@/components/metrics/requests-chart"
import { TopRulesChart } from "@/components/metrics/top-rules-chart"
import { RecentActivity } from "@/components/audit/recent-activity"
import { api } from "@/lib/api"

export default function DashboardPage() {
  // Metrics summary query (5s refresh)
  const { data: metricsSummary, isLoading: metricsLoading } = useQuery({
    queryKey: ["metrics-summary"],
    queryFn: () => api.getMetricsSummary(),
    refetchInterval: 5000, // Refresh every 5 seconds
  })

  // Recent audit events query (10s refresh)
  const { data: auditEvents, isLoading: auditLoading } = useQuery({
    queryKey: ["audit-events"],
    queryFn: () => api.getAuditEvents({ limit: 10 }),
    refetchInterval: 10000, // Refresh every 10 seconds
  })

  // Requests chart data (24h history, 1h buckets)
  const { data: requestsChartData, isLoading: requestsLoading } = useQuery({
    queryKey: ["requests-chart"],
    queryFn: () => api.getRequestChartData({ hours: 24 }),
    refetchInterval: 300000, // Refresh every 5 minutes
  })

  // Rules chart data (top rules matched)
  const { data: rulesChartData, isLoading: rulesLoading } = useQuery({
    queryKey: ["rules-chart"],
    queryFn: () => api.getRulesChartData({ days: 7 }),
    refetchInterval: 300000, // Refresh every 5 minutes
  })

  // Real-time chart data (60m history, 1m buckets)
  const { data: realtimeData } = useQuery({
    queryKey: ["realtime-chart"],
    queryFn: () => api.getRealtimeChartData({ minutes: 60 }),
    refetchInterval: 30000, // Refresh every 30 seconds
  })

  // Use real-time data if available, otherwise use 24h data
  const chartData = realtimeData || requestsChartData || []

  // Transform rules data for chart
  const topRulesData = rulesChartData?.slice(0, 5) || [
    { rule_id: "pii_ssn_us", rule_name: "US SSN", count: 0, severity: "high" as const },
    { rule_id: "secret_aws_key", rule_name: "AWS Key", count: 0, severity: "critical" as const },
    { rule_id: "pii_email", rule_name: "Email", count: 0, severity: "medium" as const },
    { rule_id: "secret_jwt", rule_name: "JWT Token", count: 0, severity: "high" as const },
    { rule_id: "pii_passport_ru", rule_name: "RU Passport", count: 0, severity: "medium" as const }
  ]

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      {/* KPI Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          title="Requests/min"
          value={metricsLoading ? "..." : metricsSummary ? metricsSummary.rps.toFixed(1) : "0.0"}
          icon={Shield}
          description="Today"
          trend={metricsSummary ? { value: 5.2, isPositive: true } : undefined}
        />
        <KpiCard
          title="Block Rate"
          value={metricsLoading ? "..." : metricsSummary ? `${metricsSummary.block_rate.toFixed(1)}%` : "0.0%"}
          icon={AlertTriangle}
          description="Blocked requests"
          trend={metricsSummary ? { value: 2.1, isPositive: false } : undefined}
        />
        <KpiCard
          title="Avg Latency"
          value={metricsLoading ? "..." : metricsSummary ? `${metricsSummary.avg_latency.toFixed(1)}ms` : "0ms"}
          icon={Clock}
          description="Response time"
          trend={metricsSummary ? { value: 3.5, isPositive: false } : undefined}
        />
        <KpiCard
          title="Cache Hit Rate"
          value={metricsLoading ? "..." : metricsSummary ? `${metricsSummary.cache_hit_rate.toFixed(1)}%` : "0.0%"}
          icon={Database}
          description="Decision cache"
          trend={metricsSummary ? { value: 8.7, isPositive: true } : undefined}
        />
      </div>

      {/* Charts */}
      <div className="grid gap-6 lg:grid-cols-2">
        <RequestsChart 
          data={chartData} 
          isLoading={requestsLoading}
          title={realtimeData ? "Real-time (last 60min)" : "Last 24 Hours"}
        />
        <TopRulesChart 
          data={topRulesData} 
          isLoading={rulesLoading}
        />
      </div>

      {/* Recent Activity */}
      <RecentActivity events={auditEvents?.items || []} isLoading={auditLoading} />
    </div>
  )
}