"use client";

import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { useApiToken } from "@/components/providers/app-providers";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { TransientDataWarning } from "@/components/transient-data-warning";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { LONG_RUNNING_REQUEST_TIMEOUT_MS, request, useConsoleStateQuery } from "@/lib/api";
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
  const { token } = useApiToken();
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useConsoleStateQuery(20_000);
  const classifierProfiles = data?.models?.classifier_profiles;
  const updateClassifierProfile = useMutation({
    mutationFn: async (profile: string) =>
      request(`/api/models/classifier-profile`, token, {
        method: "POST",
        body: JSON.stringify({
          profile,
          updated_by: "demo-ui",
        }),
        timeoutMs: LONG_RUNNING_REQUEST_TIMEOUT_MS,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["console-state"] });
    },
  });

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
  const confidenceTrend = data.incidents
    .slice(0, 12)
    .reverse()
    .map((incident) => ({
      name: incident.id.slice(0, 6),
      confidence: Number(incident.predicted_confidence ?? 0),
    }));

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Operations summary"
        title="Overview"
        description="Summary of incidents, workflow status, and service health."
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
          detail="Core platform services available"
        />
        <MetricCard
          label="Latest confidence"
          value={formatRelativeNumber(data.summary.latest_confidence)}
          detail="Confidence of the most recent predicted class"
        />
      </div>

      {classifierProfiles ? (
        <Card>
          <CardHeader>
            <CardTitle>Classifier routing</CardTitle>
            <CardDescription>
              Choose whether new classifications use the live incident-linked model or the backfill-trained model.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-[minmax(0,260px)_1fr]">
            <div className="space-y-2">
              <label className="text-sm font-medium text-[var(--text-strong)]" htmlFor="classifier-profile">
                Active model path
              </label>
              <Select
                id="classifier-profile"
                value={classifierProfiles.requested_profile ?? classifierProfiles.active_profile ?? "live"}
                disabled={updateClassifierProfile.isPending}
                onChange={(event) => {
                  const nextProfile = event.target.value;
                  if (!nextProfile || nextProfile === (classifierProfiles.requested_profile ?? classifierProfiles.active_profile)) {
                    return;
                  }
                  updateClassifierProfile.mutate(nextProfile);
                }}
              >
                {classifierProfiles.profiles.map((profile) => (
                  <option key={profile.key} value={profile.key} disabled={!profile.configured}>
                    {profile.label}
                    {!profile.configured ? " (not configured)" : ""}
                  </option>
                ))}
              </Select>
              <div className="text-xs text-[var(--text-subtle)]">
                Active profile: {titleize(classifierProfiles.active_profile ?? "unknown")}
              </div>
              {updateClassifierProfile.isError ? (
                <div className="text-xs text-[var(--danger-fg)]">
                  {updateClassifierProfile.error instanceof Error
                    ? updateClassifierProfile.error.message
                    : "Could not update classifier routing."}
                </div>
              ) : null}
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {classifierProfiles.profiles.map((profile) => (
                <div
                  key={profile.key}
                  className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-raised)] p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="font-medium text-[var(--text-strong)]">{profile.label}</div>
                      <div className="mt-1 text-sm text-[var(--text-secondary)]">{profile.description}</div>
                    </div>
                    <StatusBadge value={profile.active ? "ACTIVE" : profile.configured ? "READY" : "OFFLINE"} />
                  </div>
                  <div className="mt-3 space-y-1 text-xs text-[var(--text-subtle)]">
                    <div>Model: {profile.model_version_label || profile.model_name || "Not configured"}</div>
                    <div>Endpoint: {profile.endpoint || "Not configured"}</div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      ) : null}

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
            <CardTitle>Recent prediction confidence</CardTitle>
            <CardDescription>Shows how confident the multiclass model has been on recent incidents.</CardDescription>
          </CardHeader>
          <CardContent className="h-72">
            {confidenceTrend.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={confidenceTrend}>
                  <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                  <XAxis dataKey="name" tick={chartTickStyle} />
                  <YAxis tick={chartTickStyle} domain={[0, 1]} />
                  <Tooltip
                    contentStyle={chartTooltipContentStyle}
                    labelStyle={chartTooltipLabelStyle}
                    itemStyle={chartTooltipItemStyle}
                  />
                  <Line type="monotone" dataKey="confidence" stroke="#f97316" strokeWidth={3} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <EmptyState title="No confidence trend yet" description="Run a scenario or wait for incidents to build a prediction timeline." />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent activity</CardTitle>
            <CardDescription>Quick links to recent incidents.</CardDescription>
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
                  <span>Confidence {formatRelativeNumber(incident.predicted_confidence)}</span>
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
