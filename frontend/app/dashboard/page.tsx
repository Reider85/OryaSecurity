"use client"

import { useQuery } from "@tanstack/react-query"
import { Shield, AlertTriangle, Clock, Database } from "lucide-react"
import { KpiCard } from "@/components/metrics/kpi-card"
import { RequestsChart } from "@/components/metrics/requests-chart"
import { TopRulesChart } from "@/components/metrics/top-rules-chart"
import { RecentActivity } from "@/components/audit/recent-activity"
import { api } from "@/lib/api"

// Mock data for charts (in a real app, this would come from the backend)
const generateMockChartData = () => {
  const data = []
  const now = new Date()
  
  for (let i = 23; i >= 0; i--) {
    const timestamp = new Date(now.getTime() - i * 60 * 60 * 1000)
    const requests = Math.floor(Math.random() * 100) + 20
    const blocks = Math.floor(requests * (Math.random() * 0.3 + 0.1))
    const blockRate = Math.round((blocks / requests) * 100)
    
    data.push({
      timestamp: timestamp.toISOString(),
      requests,
      blocks,
      blockRate
    })
  }
  
  return data
}

const generateMockRulesData = () => {
  return [
    { rule_id: "pii_ssn_us", count: 45, severity: "high" as const },
    { rule_id: "aws_key", count: 32, severity: "critical" as const },
    { rule_id: "pii_email", count: 28, severity: "medium" as const },
    { rule_id: "jwt_token", count: 15, severity: "high" as const },
    { rule_id: "pii_passport_ru", count: 8, severity: "medium" as const }
  ]
}

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

  // Mock chart data
  const requestsChartData = generateMockChartData()
  const topRulesData = generateMockRulesData()

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
        <RequestsChart data={requestsChartData} />
        <TopRulesChart data={topRulesData} />
      </div>

      {/* Recent Activity */}
      <RecentActivity events={auditEvents?.items || []} />
    </div>
  )
}