import { Shield, AlertTriangle, Clock, Database } from "lucide-react";

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          title="Requests/min"
          value="--"
          icon={<Shield className="h-4 w-4 text-muted-foreground" />}
          description="Today"
        />
        <KpiCard
          title="Block Rate"
          value="--%"
          icon={<AlertTriangle className="h-4 w-4 text-muted-foreground" />}
          description="Blocked requests"
        />
        <KpiCard
          title="Avg Latency"
          value="--ms"
          icon={<Clock className="h-4 w-4 text-muted-foreground" />}
          description="Response time"
        />
        <KpiCard
          title="Cache Hit Rate"
          value="--%"
          icon={<Database className="h-4 w-4 text-muted-foreground" />}
          description="Decision cache"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="rounded-xl border bg-card p-6">
          <h2 className="text-lg font-semibold">Requests Over Time</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Chart will be rendered here with recharts.
          </p>
        </div>
        <div className="rounded-xl border bg-card p-6">
          <h2 className="text-lg font-semibold">Top Rules</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            Bar chart will be rendered here.
          </p>
        </div>
      </div>

      <div className="rounded-xl border bg-card p-6">
        <h2 className="text-lg font-semibold">Recent Activity</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Audit events table will be rendered here.
        </p>
      </div>
    </div>
  );
}

function KpiCard({
  title,
  value,
  icon,
  description,
}: {
  title: string;
  value: string;
  icon: React.ReactNode;
  description: string;
}) {
  return (
    <div className="rounded-xl border bg-card p-6">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium text-muted-foreground">{title}</p>
        {icon}
      </div>
      <p className="mt-2 text-3xl font-bold">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    </div>
  );
}
