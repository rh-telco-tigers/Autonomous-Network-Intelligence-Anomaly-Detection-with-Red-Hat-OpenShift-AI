"use client";

import * as React from "react";
import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { StatusBadge } from "@/components/status-badge";
import { TransientDataWarning } from "@/components/transient-data-warning";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConsoleStateQuery, useGuardrailsDemoRunner, useScenarioRunner } from "@/lib/api";
import { formatRelativeNumber, formatTime, titleize } from "@/lib/utils";

type GuardrailsStoryRun = {
  kind: "allow" | "review" | "block";
  incidentId?: string | null;
  title: string;
  summary: string;
};

export default function DemoPage() {
  const { data, isLoading, error } = useConsoleStateQuery(30_000);
  const scenarioRunner = useScenarioRunner();
  const guardrailsDemoRunner = useGuardrailsDemoRunner();
  const [guardrailsStoryRun, setGuardrailsStoryRun] = React.useState<GuardrailsStoryRun | null>(null);

  if (isLoading && !data) {
    return <div className="text-sm text-[var(--text-muted)]">Loading scenarios...</div>;
  }
  if (!data) {
    return <div className="text-sm text-[var(--danger-fg)]">Could not load demo scenario data.</div>;
  }
  const showRefreshWarning = Boolean(error);

  const latest = scenarioRunner.data ?? null;
  const guardrailsDemo = guardrailsDemoRunner.data ?? null;
  const isBusy = scenarioRunner.isPending || guardrailsDemoRunner.isPending;
  const latestStoryRun =
    guardrailsStoryRun ??
    (latest?.incident?.id
      ? {
          kind: "allow" as const,
          incidentId: latest.incident.id,
          title: "Guardrails allow",
          summary: "Live registration storm scenario completed with an allowed RCA path.",
        }
      : null);

  const guardrailsError = guardrailsDemoRunner.error instanceof Error ? guardrailsDemoRunner.error.message : null;
  const scenarioError = scenarioRunner.error instanceof Error ? scenarioRunner.error.message : null;

  async function runAllowStory() {
    const payload = await scenarioRunner.mutateAsync("registration_storm");
    setGuardrailsStoryRun({
      kind: "allow",
      incidentId: payload.incident?.id,
      title: "Guardrails allow",
      summary: "Live registration storm scenario completed with an allowed RCA path.",
    });
  }

  async function runGuardrailsExample(example: "review" | "block") {
    const payload = await guardrailsDemoRunner.mutateAsync(example);
    setGuardrailsStoryRun({
      kind: example,
      incidentId: payload.incident?.id,
      title: example === "review" ? "Guardrails review" : "Guardrails blocked",
      summary:
        example === "review"
          ? "Synthetic review-required RCA created with remediation unlock held for operator review."
          : "Synthetic blocked RCA created with safe fallback guidance and no remediation unlock.",
    });
  }

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
                    {titleize(String(latest.score.predicted_anomaly_type ?? latest.score.anomaly_type ?? latest.scenario))} ·{" "}
                    Confidence {formatRelativeNumber(Number(latest.score.predicted_confidence ?? 0))} ·{" "}
                    {String(latest.score.is_anomaly ? "Incident predicted" : "Normal predicted")}
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

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>Guardrails demo</CardTitle>
            <CardDescription>
              Run the customer story from one place: live allow, seeded review, and seeded blocked outcomes.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 md:grid-cols-3">
            <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium text-[var(--text-strong)]">Allow path</div>
                <StatusBadge value="Allow" />
              </div>
              <div className="mt-2 text-sm text-[var(--text-secondary)]">
                Runs a live <span className="font-medium text-[var(--text-strong)]">Registration Storm</span> scenario and
                shows the full RCA-to-remediation flow.
              </div>
              <Button className="mt-4 w-full" onClick={() => void runAllowStory()} disabled={isBusy}>
                {scenarioRunner.isPending ? "Running live scenario..." : "Run allow story"}
              </Button>
            </div>

            <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium text-[var(--text-strong)]">Review path</div>
                <StatusBadge value="Review" />
              </div>
              <div className="mt-2 text-sm text-[var(--text-secondary)]">
                Creates a fresh incident with <span className="font-medium text-[var(--text-strong)]">Guardrails require_review</span> so the UI
                can show RCA attached but automation still blocked.
              </div>
              <Button
                variant="secondary"
                className="mt-4 w-full"
                onClick={() => void runGuardrailsExample("review")}
                disabled={isBusy}
              >
                {guardrailsDemoRunner.isPending && guardrailsDemoRunner.variables === "review"
                  ? "Creating review case..."
                  : "Create review story"}
              </Button>
            </div>

            <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
              <div className="flex items-center justify-between gap-3">
                <div className="font-medium text-[var(--text-strong)]">Block path</div>
                <StatusBadge value="Blocked" />
              </div>
              <div className="mt-2 text-sm text-[var(--text-secondary)]">
                Creates a fresh incident with <span className="font-medium text-[var(--text-strong)]">Guardrails block</span> so you can show the
                safe fallback RCA and closed automation path.
              </div>
              <Button
                variant="danger"
                className="mt-4 w-full"
                onClick={() => void runGuardrailsExample("block")}
                disabled={isBusy}
              >
                {guardrailsDemoRunner.isPending && guardrailsDemoRunner.variables === "block"
                  ? "Creating blocked case..."
                  : "Create blocked story"}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Guardrails story state</CardTitle>
            <CardDescription>Open the latest guardrails walkthrough incident directly from this page.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {latestStoryRun ? (
              <>
                <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div className="font-medium text-[var(--text-strong)]">{latestStoryRun.title}</div>
                    <StatusBadge value={titleize(latestStoryRun.kind)} />
                  </div>
                  <div className="mt-2 text-sm text-[var(--text-secondary)]">{latestStoryRun.summary}</div>
                  {latestStoryRun.incidentId ? (
                    <div className="mt-2 text-xs text-[var(--text-subtle)]">Incident {latestStoryRun.incidentId}</div>
                  ) : null}
                </div>
                {latestStoryRun.incidentId ? (
                  <Button asChild className="w-full">
                    <Link href={`/incidents/${latestStoryRun.incidentId}`}>Open guardrails incident</Link>
                  </Button>
                ) : null}
              </>
            ) : (
              <div className="text-sm text-[var(--text-subtle)]">
                No guardrails story has been launched from this page yet.
              </div>
            )}

            {guardrailsDemo?.incident?.id ? (
              <div className="rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-subtle)] p-4 text-sm text-[var(--text-secondary)]">
                Guardrails demo incident created:{" "}
                <Link href={`/incidents/${guardrailsDemo.incident.id}`} className="font-medium text-[var(--accent)]">
                  {guardrailsDemo.incident.id}
                </Link>
              </div>
            ) : null}

            {guardrailsError ? (
              <div className="rounded-2xl border border-[var(--danger-ring)] bg-[var(--danger-bg)] p-4 text-sm text-[var(--danger-fg)]">
                {guardrailsError}
              </div>
            ) : null}
            {scenarioError ? (
              <div className="rounded-2xl border border-[var(--danger-ring)] bg-[var(--danger-bg)] p-4 text-sm text-[var(--danger-fg)]">
                {scenarioError}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
