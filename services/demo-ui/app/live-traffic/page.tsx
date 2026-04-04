"use client";

import Link from "next/link";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConsoleStateQuery } from "@/lib/api";
import { formatRelativeNumber, formatTime, titleize } from "@/lib/utils";

export default function LiveTrafficPage() {
  const { data, isLoading, error } = useConsoleStateQuery(5_000);

  if (isLoading) {
    return <div className="text-sm text-slate-400">Loading traffic stream...</div>;
  }
  if (error || !data) {
    return <div className="text-sm text-rose-300">Could not load live traffic data.</div>;
  }

  const trafficTrend = data.traffic_stream
    .slice(0, 18)
    .reverse()
    .map((item) => ({
      time: new Date(item.executed_at).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit" }),
      requests: item.traffic_preview.stats.requests_per_second,
      anomalyScore: Number(item.anomaly_score ?? 0),
    }));
  const fallbackTrend = trafficTrend.length
    ? trafficTrend
    : [
        {
          time: "Now",
          requests: data.traffic_preview.stats.requests_per_second,
          anomalyScore: Number(data.summary.latest_score ?? 0),
        },
      ];
  const hasSnapshotRows = data.traffic_preview.rows.length > 0;

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Traffic stream"
        title="Live Traffic"
        description="A dedicated traffic stream page for normal and anomalous windows. No remediation controls here."
      />

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Current scenario</CardTitle>
            <CardDescription>The latest live traffic snapshot attached to the console state.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold text-[var(--text-strong)]">
              {titleize(data.traffic_preview.display_name ?? data.traffic_preview.scenario_name ?? "unknown")}
            </div>
            <div className="mt-1 text-sm text-[var(--text-secondary)]">
              {data.traffic_preview.source ?? "derived"}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Request rate</CardTitle>
            <CardDescription>Current requests per second from the latest snapshot.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold text-[var(--text-strong)]">
              {formatRelativeNumber(data.traffic_preview.stats.requests_per_second)} req/s
            </div>
            <div className="mt-1 text-sm text-[var(--text-secondary)]">
              Retry ratio {formatRelativeNumber(data.traffic_preview.stats.retry_ratio)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Active node</CardTitle>
            <CardDescription>The node currently associated with the latest preview.</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold text-[var(--text-strong)]">{data.traffic_preview.stats.active_node}</div>
            <div className="mt-1 text-sm text-[var(--text-secondary)]">
              {data.traffic_stream.length ? "Streaming event history available below." : "Showing the latest snapshot while the event stream refills."}
            </div>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <Card>
          <CardHeader>
            <CardTitle>Traffic rate trend</CardTitle>
            <CardDescription>Recent windows across normal and problematic traffic, regardless of incident state.</CardDescription>
          </CardHeader>
          <CardContent className="h-72">
            {fallbackTrend.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={fallbackTrend}>
                  <CartesianGrid stroke="#1e293b" vertical={false} />
                  <XAxis dataKey="time" tick={{ fill: "#94a3b8", fontSize: 12 }} />
                  <YAxis tick={{ fill: "#94a3b8", fontSize: 12 }} />
                  <Tooltip />
                  <Area type="monotone" dataKey="requests" stroke="#38bdf8" fill="#0ea5e933" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <EmptyState title="No traffic windows yet" description="Traffic windows will appear after scenarios or live traffic runs." />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Latest packet sample</CardTitle>
            <CardDescription>The most recent traffic preview currently attached to the console state.</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="overflow-x-auto rounded-2xl border border-slate-800 bg-slate-900/60 p-4 text-sm text-slate-300">
              {data.traffic_preview.packet_sample || "No packet sample available."}
            </pre>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Latest traffic snapshot</CardTitle>
          <CardDescription>Shows the most recent preview even when no recent scenario-execution events are available.</CardDescription>
        </CardHeader>
        <CardContent>
          {hasSnapshotRows ? (
            <div className="overflow-hidden rounded-2xl border border-slate-800">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-900/80 text-slate-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Time</th>
                    <th className="px-4 py-3 font-medium">Method</th>
                    <th className="px-4 py-3 font-medium">Path</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {data.traffic_preview.rows.map((row) => (
                    <tr key={`${row.sequence}-${row.time}`} className="border-t border-slate-800 bg-slate-950/40">
                      <td className="px-4 py-3 text-slate-400">{row.time}</td>
                      <td className="px-4 py-3 font-medium text-slate-50">{row.method}</td>
                      <td className="px-4 py-3 text-slate-300">{row.path}</td>
                      <td className="px-4 py-3">
                        <StatusBadge value={row.status} />
                      </td>
                      <td className="px-4 py-3 text-slate-300">{formatRelativeNumber(row.latency_ms)} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <EmptyState title="No preview rows yet" description="The latest traffic snapshot will appear here after traffic is observed." />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent traffic windows</CardTitle>
          <CardDescription>Normal and anomalous scenario windows are shown together as the operating stream.</CardDescription>
        </CardHeader>
        <CardContent>
          {!data.traffic_stream.length ? (
            <EmptyState title="No traffic events yet" description="Run a scenario or wait for traffic to populate the stream." />
          ) : (
            <div className="overflow-hidden rounded-2xl border border-slate-800">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-900/80 text-slate-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Scenario</th>
                    <th className="px-4 py-3 font-medium">Traffic</th>
                    <th className="px-4 py-3 font-medium">Anomaly</th>
                    <th className="px-4 py-3 font-medium">Incident</th>
                    <th className="px-4 py-3 font-medium">Window time</th>
                  </tr>
                </thead>
                <tbody>
                  {data.traffic_stream.map((item) => (
                    <tr key={`${item.executed_at}-${item.scenario}`} className="border-t border-slate-800 bg-slate-950/40">
                      <td className="px-4 py-3">
                        <div className="font-medium text-slate-50">{titleize(item.scenario)}</div>
                        <div className="text-xs text-slate-500">{item.feature_source}</div>
                      </td>
                      <td className="px-4 py-3">
                        {formatRelativeNumber(item.traffic_preview.stats.requests_per_second)} req/s · retry{" "}
                        {formatRelativeNumber(item.traffic_preview.stats.retry_ratio)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <StatusBadge value={item.is_anomaly ? "Anomaly" : "Normal"} />
                          <span className="text-slate-300">{formatRelativeNumber(item.anomaly_score)}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        {item.incident_id ? (
                          <Link href={`/incidents/${item.incident_id}`} className="text-sky-300">
                            Open incident
                          </Link>
                        ) : (
                          <span className="text-slate-500">No incident</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-400">{formatTime(item.executed_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
