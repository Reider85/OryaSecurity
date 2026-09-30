"use client"

import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface RuleMatch {
  rule_id: string
  count: number
  severity: "low" | "medium" | "high" | "critical"
}

interface TopRulesChartProps {
  data: RuleMatch[]
}

const SEVERITY_COLORS = {
  low: "#22c55e",      // green
  medium: "#f59e0b",   // amber
  high: "#f97316",     // orange
  critical: "#dc2626"  // red
}

export function TopRulesChart({ data }: TopRulesChartProps) {
  const sortedData = data
    .sort((a, b) => b.count - a.count)
    .slice(0, 5)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Top 5 Triggered Rules (Today)</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={sortedData} margin={{ top: 20, right: 30, left: 20, bottom: 60 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis 
              dataKey="rule_id" 
              angle={-45}
              textAnchor="end"
              height={80}
              interval={0}
              tick={{ fontSize: 12 }}
            />
            <YAxis />
            <Tooltip 
              formatter={(value, name) => [
                value, 
                name === "count" ? "Matches" : name
              ]}
              labelFormatter={(value) => `Rule: ${value}`}
            />
            <Bar dataKey="count" name="Matches">
              {sortedData.map((entry, index) => (
                <Cell 
                  key={`cell-${index}`} 
                  fill={SEVERITY_COLORS[entry.severity]} 
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <div className="flex justify-center mt-4 space-x-4 text-xs">
          <div className="flex items-center">
            <div className="w-3 h-3 bg-red-600 rounded mr-1"></div>
            <span>Critical</span>
          </div>
          <div className="flex items-center">
            <div className="w-3 h-3 bg-orange-500 rounded mr-1"></div>
            <span>High</span>
          </div>
          <div className="flex items-center">
            <div className="w-3 h-3 bg-amber-500 rounded mr-1"></div>
            <span>Medium</span>
          </div>
          <div className="flex items-center">
            <div className="w-3 h-3 bg-green-500 rounded mr-1"></div>
            <span>Low</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}