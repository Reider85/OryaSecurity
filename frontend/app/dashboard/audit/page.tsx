"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export default function AuditPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Audit Log</h1>
        <Button variant="outline">Export CSV</Button>
      </div>

      <div className="flex gap-2">
        <Input placeholder="Search by prompt hash..." className="max-w-sm" />
        <Button variant="outline">Apply</Button>
        <Button variant="ghost">Reset</Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Events</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Time</TableHead>
                <TableHead>Request ID</TableHead>
                <TableHead>Prompt Hash</TableHead>
                <TableHead>Verdict</TableHead>
                <TableHead>Reason</TableHead>
                <TableHead>Latency (ms)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                  No audit events yet.
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
