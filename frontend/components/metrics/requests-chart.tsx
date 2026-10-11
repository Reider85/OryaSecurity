"use client"

import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from "recharts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface RequestsDataPoint {
  timestamp: string
  requests: number
  blocks: number
  blockRate: number
}

interface RequestsChartProps {
  data: RequestsDataPoint[]
  isLoading?: boolean
  title?: string
}

export function RequestsChart({ data, isLoading, title }: RequestsChartProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{title || "Requests Over Time (Last 24 Hours)"}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center h-[300px]">
            <div className="animate-pulse space-y-4">
              <div className="h-4 bg-gray-200 rounded w-3/4"></div>
              <div className="h-4 bg-gray-200 rounded w-1/2"></div>
              <div className="h-4 bg-gray-200 rounded w-2/3"></div>
            </div>
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title || "Requests Over Time (Last 24 Hours)"}</CardTitle>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis 
              dataKey="timestamp" 
              tickFormatter={(value) => {
                const date = new Date(value)
                return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
              }}
            />
            <YAxis yAxisId="left" />
            <YAxis yAxisId="right" orientation="right" />
            <Tooltip 
              labelFormatter={(value) => {
                const date = new Date(value)
                return date.toLocaleString()
              }}
            />
            <Legend />
            <Line 
              yAxisId="left"
              type="monotone" 
              dataKey="requests" 
              stroke="#2563eb" 
              strokeWidth={2}
              name="Total Requests"
            />
            <Line 
              yAxisId="left"
              type="monotone" 
              dataKey="blocks" 
              stroke="#dc2626" 
              strokeWidth={2}
              name="Blocked Requests"
            />
            <Line 
              yAxisId="right"
              type="monotone" 
              dataKey="blockRate" 
              stroke="#16a34a" 
              strokeWidth={2}
              name="Block Rate (%)"
            />
          </LineChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}