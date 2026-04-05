"use client";

import Link from "next/link";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { TransientDataWarning } from "@/components/transient-data-warning";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConsoleStateQuery } from "@/lib/api";
import { formatInteger, formatRelativeNumber, formatTime, titleize } from "@/lib/utils";

const chartPalette = ["#38bdf8", "#f97316", "#ef4444", "#10b981", "#8b5cf6", "#facc15", "#14b8a6", "#fb7185"];
const chartTickStyle = { fill: "var(--chart-axis)", fontSize: 12 };
const chartTooltipContentStyle = {
  backgroundColor: "var(--chart-tooltip-bg)",
  borderColor: "var(--chart-tooltip-border)",
  borderRadius: 16,
  color: "var(--chart-tooltip-text)",
};
const chartTooltipLabelStyle = { color: "var(--chart-tooltip-label)", fontWeight: 600 };
const chartTooltipItemStyle = { color: "var(--chart-tooltip-text)" };

export default function OverviewPage() {
  const { data, isLoading, error } = useConsoleStateQuery(20_000);

  if (isLoading && !data) {
    return <div className="text-sm text-[var(--text-muted)]">Loading overview...</div>;
  }
  if (!data) {
    return <div className="text-sm text-[var(--danger-fg)]">Could not load overview data.</div>;
  }
  const showRefreshWarning = Boolean(error);

  const incidentMix = data.incidents.reduce<Array<{ name: string; value: number }>>((acc, incident) => {
    const key = incident.anomaly_type;
    const existing = acc.find((item) => item.name === key);
    if (existing) {
      existing.value += 1;
    } else {
      acc.push({ name: key, value: 1 });
    }
    return acc;
  }, []);
  const scoreTrend = data.incidents
    .slice(0, 12)
    .reverse()
    .map((incident) => ({
      name: incident.id.slice(0, 6),
      score: Number(incident.anomaly_score ?? 0),
    }));

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Operations summary"
        title="Overview"
        description="High-level incident, traffic, and service posture without repeating incident workflow controls."
      />
      {showRefreshWarning ? (
        <TransientDataWarning>
          Showing the last successful overview snapshot while the next background refresh is retried.
        </TransientDataWarning>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard label="Active incidents" value={formatInteger(data.summary.active_incident_count)} detail="Workflow states that still need attention" />
        <MetricCard label="Critical incidents" value={formatInteger(data.summary.critical_incidents)} detail="Highest severity anomalies currently active" />
        <MetricCard
          label="Healthy services"
          value={`${formatInteger(data.summary.healthy_services)}/${formatInteger(data.summary.service_count)}`}
          detail="Platform, traffic, anomaly, RCA, and serving path"
        />
        <MetricCard
          label="Traffic windows"
          value={formatInteger(data.traffic_stream.length)}
          detail="Recent normal and anomalous scenario windows"
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_0.8fr]">
        <Card>
          <CardHeader>
            <CardTitle>Workflow state distribution</CardTitle>
            <CardDescription>Shows how incidents move through the operational workflow.</CardDescription>
          </CardHeader>
          <CardContent className="h-80">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.summary.workflow_state_distribution}>
                <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                <XAxis dataKey="state" tick={chartTickStyle} interval={0} angle={-25} height={80} />
                <YAxis tick={chartTickStyle} />
                <Tooltip
                  contentStyle={chartTooltipContentStyle}
                  labelStyle={chartTooltipLabelStyle}
                  itemStyle={chartTooltipItemStyle}
                />
                <Bar dataKey="count" fill="#38bdf8" radius={[8, 8, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Incident mix</CardTitle>
            <CardDescription>Category share across the incidents currently loaded by the platform.</CardDescription>
          </CardHeader>
          <CardContent className="h-80">
            {incidentMix.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={incidentMix} dataKey="value" nameKey="name" innerRadius={60} outerRadius={100}>
                    {incidentMix.map((entry, index) => (
                      <Cell key={entry.name} fill={chartPalette[index % chartPalette.length]} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={chartTooltipContentStyle}
                    labelStyle={chartTooltipLabelStyle}
                    itemStyle={chartTooltipItemStyle}
                    formatter={(value, name) => [
                      Number(value ?? 0),
                      titleize(String(name ?? "")),
                    ]}
                  />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <EmptyState title="No incident mix yet" description="Incident distribution will appear when anomalies are recorded." />
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <Card>
          <CardHeader>
            <CardTitle>Recent anomaly score trend</CardTitle>
            <CardDescription>Recent incident scores to spot when the serving path is seeing stronger anomalies.</CardDescription>
          </CardHeader>
          <CardContent className="h-72">
            {scoreTrend.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={scoreTrend}>
                  <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                  <XAxis dataKey="name" tick={chartTickStyle} />
                  <YAxis tick={chartTickStyle} domain={[0, 1]} />
                  <Tooltip
                    contentStyle={chartTooltipContentStyle}
                    labelStyle={chartTooltipLabelStyle}
                    itemStyle={chartTooltipItemStyle}
                  />
                  <Line type="monotone" dataKey="score" stroke="#f97316" strokeWidth={3} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <EmptyState title="No score trend yet" description="Run a scenario or wait for incidents to build a score timeline." />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
            <CardDescription>Quick links into the incident workflow without repeating the full detail page here.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {data.incidents.slice(0, 5).map((incident) => (
              <Link
                key={incident.id}
                href={`/incidents/${incident.id}`}
                className="block rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4 transition-colors hover:bg-[var(--surface-hover)]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-medium text-[var(--text-strong)]">{titleize(incident.anomaly_type)}</div>
                    <div className="mt-1 text-sm text-[var(--text-secondary)]">
                      {incident.subtitle ?? incident.impact ?? "Incident workflow item"}
                    </div>
                  </div>
                  <StatusBadge value={incident.status} />
                </div>
                <div className="mt-3 flex items-center justify-between text-xs text-[var(--text-subtle)]">
                  <span>Score {formatRelativeNumber(incident.anomaly_score)}</span>
                  <span>{formatTime(incident.updated_at)}</span>
                </div>
              </Link>
            ))}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function MetricCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <Card>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-3xl">{value}</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-[var(--text-muted)]">{detail}</CardContent>
    </Card>
  );
}
