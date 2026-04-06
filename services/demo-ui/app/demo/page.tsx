"use client";

import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { TransientDataWarning } from "@/components/transient-data-warning";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConsoleStateQuery, useScenarioRunner } from "@/lib/api";
import { formatRelativeNumber, formatTime, titleize } from "@/lib/utils";

export default function DemoPage() {
  const { data, isLoading, error } = useConsoleStateQuery(30_000);
  const scenarioRunner = useScenarioRunner();

  if (isLoading && !data) {
    return <div className="text-sm text-[var(--text-muted)]">Loading scenarios...</div>;
  }
  if (!data) {
    return <div className="text-sm text-[var(--danger-fg)]">Could not load demo scenario data.</div>;
  }
  const showRefreshWarning = Boolean(error);

  const latest = scenarioRunner.data ?? null;

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Demo operations"
        title="Run Scenarios"
        description="Run test scenarios to generate traffic and sample incidents."
      />
      {showRefreshWarning ? (
        <TransientDataWarning>
          Showing the last successful scenario snapshot while the background refresh reconnects.
        </TransientDataWarning>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <Card>
          <CardHeader>
            <CardTitle>Scenario catalog</CardTitle>
            <CardDescription>Run normal and anomalous scenarios on demand.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-2">
            {data.scenarios.map((scenario) => (
              <button
                key={scenario.scenario_name}
                className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-left transition-colors hover:bg-[var(--surface-hover)]"
                onClick={() => scenarioRunner.mutate(scenario.scenario_name)}
                disabled={scenarioRunner.isPending}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="font-medium text-[var(--text-strong)]">{scenario.display_name}</div>
                  <StatusBadge value={scenario.is_nominal ? "Normal" : "Scenario"} />
                </div>
                <div className="mt-2 text-sm text-[var(--text-secondary)]">{scenario.description}</div>
              </button>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Latest execution</CardTitle>
            <CardDescription>Most recent result from a scenario run in this browser session.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {latest ? (
              <>
                <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <div className="font-medium text-[var(--text-strong)]">{titleize(latest.scenario)}</div>
                  <div className="mt-1 text-sm text-[var(--text-secondary)]">
                    Score {formatRelativeNumber(Number(latest.score.anomaly_score ?? 0))} ·{" "}
                    {String(latest.score.is_anomaly ? "Anomaly detected" : "No incident")}
                  </div>
                  <div className="mt-2 text-xs text-[var(--text-subtle)]">{formatTime(new Date().toISOString())}</div>
                </div>
                {latest.incident?.id ? (
                  <Button asChild className="w-full">
                    <Link href={`/incidents/${latest.incident.id}`}>Open created incident</Link>
                  </Button>
                ) : null}
              </>
            ) : (
              <div className="text-sm text-[var(--text-subtle)]">No scenario has been run from this page yet.</div>
            )}
            <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-sm text-[var(--text-secondary)]">
              {scenarioRunner.isPending
                ? "Running scenario: generating features, scoring the anomaly, creating analysis, and updating the incident."
                : "Scenario execution updates the incident queue and shared platform state."}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
