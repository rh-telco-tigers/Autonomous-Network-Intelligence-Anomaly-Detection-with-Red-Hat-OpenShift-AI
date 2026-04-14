#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests
import urllib3
import yaml


DEFAULT_HOST = "https://control-plane-ani-runtime.apps.ocp.8j66v.sandbox381.opentlc.com"
DEFAULT_PROJECT = "ani-demo"
DEFAULT_REQUESTED_BY = "codex-playbook-validator"
AI_PLAYBOOK_GENERATION_ACTION = "generate_ai_ansible_playbook"


@dataclass
class ScenarioResult:
    scenario: str
    status: str
    incident_id: str = ""
    remediation_id: int | None = None
    generation_status: str = ""
    playbook_ref: str = ""
    action_ref: str = ""
    error: str = ""
    detail: str = ""
    elapsed_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario": self.scenario,
            "status": self.status,
            "incident_id": self.incident_id,
            "remediation_id": self.remediation_id,
            "generation_status": self.generation_status,
            "playbook_ref": self.playbook_ref,
            "action_ref": self.action_ref,
            "error": self.error,
            "detail": self.detail,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


class ControlPlaneClient:
    def __init__(self, host: str, api_key: str, *, verify: bool) -> None:
        self.host = host.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key, "Content-Type": "application/json"})
        self.verify = verify

    def get(self, path: str, *, params: Dict[str, Any] | None = None) -> requests.Response:
        return self.session.get(f"{self.host}{path}", params=params, verify=self.verify, timeout=60)

    def post(self, path: str, payload: Dict[str, Any]) -> requests.Response:
        return self.session.post(f"{self.host}{path}", json=payload, verify=self.verify, timeout=60)

    def json_get(self, path: str, *, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        response = self.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def json_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self.post(path, payload)
        response.raise_for_status()
        return response.json()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the live AI playbook-generation path against the current scenario matrix.",
    )
    parser.add_argument("--host", default=os.getenv("CONTROL_PLANE_HOST", DEFAULT_HOST))
    parser.add_argument("--api-key", default=os.getenv("CONTROL_PLANE_API_TOKEN", "demo-token"))
    parser.add_argument("--project", default=os.getenv("DEMO_PROJECT", DEFAULT_PROJECT))
    parser.add_argument("--requested-by", default=os.getenv("PLAYBOOK_VALIDATOR_REQUESTED_BY", DEFAULT_REQUESTED_BY))
    parser.add_argument("--source-url", default=os.getenv("PLAYBOOK_VALIDATOR_SOURCE_URL", ""))
    parser.add_argument("--scenario", action="append", dest="scenarios", default=[])
    parser.add_argument("--timeout-seconds", type=int, default=int(os.getenv("PLAYBOOK_VALIDATOR_TIMEOUT_SECONDS", "240")))
    parser.add_argument("--poll-seconds", type=float, default=float(os.getenv("PLAYBOOK_VALIDATOR_POLL_SECONDS", "5")))
    parser.add_argument("--strict-normal", action="store_true", help="Fail if the normal scenario creates an incident.")
    parser.add_argument("--output", default="", help="Optional path to write the JSON report.")
    parser.add_argument("--verify-tls", action="store_true", help="Enable TLS verification for control-plane requests.")
    return parser.parse_args()


def _catalog_scenarios(client: ControlPlaneClient, project: str) -> List[str]:
    try:
        payload = client.json_get("/console/state", params={"project": project})
        scenarios = [str(item.get("scenario_name") or "").strip() for item in payload.get("scenarios", [])]
        return [item for item in scenarios if item]
    except Exception:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from services.shared.incident_taxonomy import console_scenario_names  # type: ignore

        return list(console_scenario_names())


def _post_run_scenario(client: ControlPlaneClient, project: str, scenario: str) -> Dict[str, Any]:
    return client.json_post("/console/run-scenario", {"scenario": scenario, "project": project})


def _wait_for_remediations(
    client: ControlPlaneClient,
    incident_id: str,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        response = client.post(f"/incidents/{incident_id}/remediation/generate", {})
        if response.status_code == 200:
            return response.json()
        if response.status_code == 400 and "RCA must exist" in response.text:
            last_error = response.text
            time.sleep(poll_seconds)
            continue
        response.raise_for_status()
    raise TimeoutError(f"Timed out waiting for RCA/remediations for {incident_id}: {last_error}")


def _find_ai_request_remediation(remediations: Iterable[Dict[str, Any]]) -> Dict[str, Any] | None:
    for remediation in remediations:
        action_ref = str(remediation.get("action_ref") or "").strip()
        generation_kind = str(remediation.get("generation_kind") or "").strip()
        if action_ref == AI_PLAYBOOK_GENERATION_ACTION or generation_kind == "request":
            return remediation
    return None


def _request_playbook_generation(
    client: ControlPlaneClient,
    incident_id: str,
    remediation_id: int,
    *,
    requested_by: str,
    source_url: str,
    note: str,
) -> Dict[str, Any]:
    return client.json_post(
        f"/incidents/{incident_id}/remediation/{remediation_id}/generate-playbook",
        {
            "requested_by": requested_by,
            "notes": note,
            "source_url": source_url,
        },
    )


def _poll_generated_remediation(
    client: ControlPlaneClient,
    incident_id: str,
    remediation_id: int,
    *,
    timeout_seconds: int,
    poll_seconds: float,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_snapshot: Dict[str, Any] | None = None
    while time.monotonic() < deadline:
        payload = client.json_get(f"/incidents/{incident_id}")
        current = payload.get("current_remediations") or []
        for remediation in current:
            if int(remediation.get("id") or 0) != remediation_id:
                continue
            last_snapshot = remediation
            status = str(remediation.get("generation_status") or "").strip().lower()
            if status in {"generated", "failed"}:
                return remediation
        time.sleep(poll_seconds)
    raise TimeoutError(
        f"Timed out waiting for callback for incident {incident_id} remediation {remediation_id}. "
        f"Last snapshot: {json.dumps(last_snapshot or {}, sort_keys=True)}"
    )


def _validate_generated_yaml(playbook_yaml: str) -> str:
    payload = yaml.safe_load(playbook_yaml)
    if not isinstance(payload, list) or not payload:
        raise ValueError("Generated playbook is not a non-empty YAML list of plays")
    first_play = payload[0]
    if not isinstance(first_play, dict):
        raise ValueError("First play is not a YAML mapping")
    play_name = str(first_play.get("name") or "").strip()
    if not play_name:
        raise ValueError("First play has no name")
    return play_name


def _ansible_syntax_check(playbook_yaml: str) -> str:
    binary = shutil.which("ansible-playbook")
    if not binary:
        return "skipped"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
        handle.write(playbook_yaml)
        temp_path = handle.name
    try:
        completed = subprocess.run(
            [binary, temp_path, "-i", "localhost,", "-c", "local", "--syntax-check"],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            raise ValueError(f"ansible-playbook --syntax-check failed: {stderr}")
        return "passed"
    finally:
        Path(temp_path).unlink(missing_ok=True)


def _scenario_note(scenario: str) -> str:
    return (
        f"Live validator run for scenario {scenario}. "
        "Fail closed if the requested playbook cannot be grounded to supported ani-sipp primitives."
    )


def _validate_one_scenario(
    client: ControlPlaneClient,
    *,
    scenario: str,
    project: str,
    requested_by: str,
    source_url: str,
    timeout_seconds: int,
    poll_seconds: float,
    strict_normal: bool,
) -> ScenarioResult:
    started = time.monotonic()
    run_payload = _post_run_scenario(client, project, scenario)
    incident = run_payload.get("incident") or {}
    incident_id = str(incident.get("id") or "").strip()
    score = run_payload.get("score") or {}
    is_anomaly = bool(score.get("is_anomaly"))
    if scenario == "normal" and not incident_id:
        return ScenarioResult(
            scenario=scenario,
            status="passed_no_incident",
            detail="Normal scenario did not create an incident, so playbook generation was correctly not invoked.",
            elapsed_seconds=time.monotonic() - started,
        )
    if scenario == "normal" and incident_id and strict_normal:
        return ScenarioResult(
            scenario=scenario,
            status="failed",
            incident_id=incident_id,
            error="Normal scenario created an incident under strict mode.",
            elapsed_seconds=time.monotonic() - started,
        )
    if not incident_id:
        return ScenarioResult(
            scenario=scenario,
            status="failed",
            error="Scenario did not produce an incident, so playbook generation could not be exercised.",
            detail=f"score.is_anomaly={is_anomaly}",
            elapsed_seconds=time.monotonic() - started,
        )

    remediation_payload = _wait_for_remediations(
        client,
        incident_id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
    remediations = remediation_payload.get("remediations") or []
    ai_request = _find_ai_request_remediation(remediations)
    if not ai_request:
        return ScenarioResult(
            scenario=scenario,
            status="failed",
            incident_id=incident_id,
            error="AI playbook generation remediation was not present.",
            elapsed_seconds=time.monotonic() - started,
        )

    remediation_id = int(ai_request.get("id") or 0)
    _request_playbook_generation(
        client,
        incident_id,
        remediation_id,
        requested_by=requested_by,
        source_url=source_url,
        note=_scenario_note(scenario),
    )
    generated = _poll_generated_remediation(
        client,
        incident_id,
        remediation_id,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )

    generation_status = str(generated.get("generation_status") or "").strip().lower()
    playbook_yaml = str(generated.get("playbook_yaml") or "").strip()
    playbook_ref = str(generated.get("playbook_ref") or "").strip()
    action_ref = str(generated.get("action_ref") or "").strip()
    if generation_status != "generated":
        return ScenarioResult(
            scenario=scenario,
            status="failed",
            incident_id=incident_id,
            remediation_id=remediation_id,
            generation_status=generation_status,
            playbook_ref=playbook_ref,
            action_ref=action_ref,
            error=str(generated.get("generation_error") or "Generation callback did not complete successfully."),
            elapsed_seconds=time.monotonic() - started,
        )
    if not playbook_yaml:
        return ScenarioResult(
            scenario=scenario,
            status="failed",
            incident_id=incident_id,
            remediation_id=remediation_id,
            generation_status=generation_status,
            playbook_ref=playbook_ref,
            action_ref=action_ref,
            error="Generated remediation has empty playbook_yaml.",
            elapsed_seconds=time.monotonic() - started,
        )

    play_name = _validate_generated_yaml(playbook_yaml)
    syntax_status = _ansible_syntax_check(playbook_yaml)
    return ScenarioResult(
        scenario=scenario,
        status="passed",
        incident_id=incident_id,
        remediation_id=remediation_id,
        generation_status=generation_status,
        playbook_ref=playbook_ref,
        action_ref=action_ref,
        detail=f"play={play_name}; ansible_syntax={syntax_status}",
        elapsed_seconds=time.monotonic() - started,
    )


def _write_output(path: str, payload: Dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    args = _parse_args()
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    client = ControlPlaneClient(args.host, args.api_key, verify=args.verify_tls)
    scenarios = args.scenarios or _catalog_scenarios(client, args.project)
    started = time.monotonic()
    results: List[ScenarioResult] = []

    for scenario in scenarios:
        print(f"[matrix] scenario={scenario} starting", flush=True)
        try:
            result = _validate_one_scenario(
                client,
                scenario=scenario,
                project=args.project,
                requested_by=args.requested_by,
                source_url=args.source_url,
                timeout_seconds=args.timeout_seconds,
                poll_seconds=args.poll_seconds,
                strict_normal=args.strict_normal,
            )
        except Exception as exc:  # noqa: BLE001
            result = ScenarioResult(
                scenario=scenario,
                status="failed",
                error=str(exc),
            )
        results.append(result)
        print(
            f"[matrix] scenario={scenario} status={result.status} incident={result.incident_id or '-'} detail={result.detail or result.error or '-'}",
            flush=True,
        )

    passed = sum(1 for item in results if item.status.startswith("passed"))
    failed = sum(1 for item in results if item.status == "failed")
    payload = {
        "host": args.host,
        "project": args.project,
        "requested_by": args.requested_by,
        "scenario_count": len(results),
        "passed": passed,
        "failed": failed,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "results": [item.to_dict() for item in results],
    }
    _write_output(args.output, payload)
    print(json.dumps(payload, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
