"use client"

import { format } from "date-fns"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { AuditEventResponse } from "@/lib/api"

interface RecentActivityProps {
  events: AuditEventResponse[]
  isLoading?: boolean
}

export function RecentActivity({ events, isLoading }: RecentActivityProps) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Recent Activity</CardTitle>
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
        <CardTitle>Recent Activity</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Request ID</TableHead>
                <TableHead>Verdict</TableHead>
                <TableHead>Latency</TableHead>
                <TableHead>Rules Matched</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="text-center py-4 text-muted-foreground">
                    No audit events found
                  </TableCell>
                </TableRow>
              ) : (
                events.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell className="font-medium">
                      {format(new Date(event.ts), "MMM d, HH:mm:ss")}
                    </TableCell>
                    <TableCell className="font-mono text-sm">
                      {event.request_id.slice(0, 8)}...
                    </TableCell>
                    <TableCell>
                      <Badge 
                        variant={event.verdict === "allow" ? "default" : "destructive"}
                      >
                        {event.verdict.toUpperCase()}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      {event.latency_ms ? `${event.latency_ms}ms` : "-"}
                    </TableCell>
                    <TableCell>
                      {event.rules_matched && event.rules_matched.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {event.rules_matched.slice(0, 3).map((rule, index) => (
                            <Badge key={index} variant="outline" className="text-xs">
                              {rule.rule_id}
                            </Badge>
                          ))}
                          {event.rules_matched.length > 3 && (
                            <Badge variant="outline" className="text-xs">
                              +{event.rules_matched.length - 3}
                            </Badge>
                          )}
                        </div>
                      ) : (
                        <span className="text-muted-foreground">None</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  )
}