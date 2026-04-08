import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

if "prometheus_client" not in sys.modules:
    prometheus_client = types.ModuleType("prometheus_client")

    class _NoopMetric:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def labels(self, *_args: object, **_kwargs: object) -> "_NoopMetric":
            return self

        def inc(self, *_args: object, **_kwargs: object) -> None:
            return None

        def set(self, *_args: object, **_kwargs: object) -> None:
            return None

        def observe(self, *_args: object, **_kwargs: object) -> None:
            return None

    prometheus_client.CONTENT_TYPE_LATEST = "text/plain"
    prometheus_client.Counter = _NoopMetric
    prometheus_client.Gauge = _NoopMetric
    prometheus_client.Histogram = _NoopMetric
    prometheus_client.generate_latest = lambda: b""
    sys.modules["prometheus_client"] = prometheus_client

MODULE_PATH = Path(__file__).resolve().parents[1] / "app.py"
SPEC = importlib.util.spec_from_file_location("control_plane_app", MODULE_PATH)
assert SPEC and SPEC.loader
control_plane_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control_plane_app)


class _TaskRecorder:
    def __init__(self) -> None:
        self.tasks: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def add_task(self, func: object, *args: object, **kwargs: object) -> None:
        self.tasks.append((func, args, kwargs))


class PlaneEscalationRemediationTests(unittest.TestCase):
    def test_execute_open_plane_escalation_creates_plane_ticket(self) -> None:
        incident_state = {
            "incident": {
                "id": "inc-123",
                "project": "ims-demo",
                "status": control_plane_app.REMEDIATION_SUGGESTED,
                "workflow_revision": 1,
            },
            "ticket": None,
        }
        remediation = {
            "id": 7,
            "action_ref": "open_plane_escalation",
            "action_mode": "notify",
            "title": "Escalate to Plane for human coordination",
            "status": "pending",
        }
        sync_result = {
            "id": 99,
            "provider": "plane",
            "external_key": "PLANE-42",
            "operation": {"status": "created"},
        }

        def get_incident(_: str) -> dict[str, object]:
            return dict(incident_state["incident"])

        def transition(incident: dict[str, object], target_state: str, _: str, __: str) -> dict[str, object]:
            updated = dict(incident)
            updated["status"] = target_state
            updated["workflow_revision"] = int(updated.get("workflow_revision") or 1) + 1
            incident_state["incident"] = updated
            return updated

        def record_incident_action(**kwargs: object) -> dict[str, object]:
            return {
                "id": 501,
                "execution_status": kwargs["execution_status"],
                "result_summary": kwargs["result_summary"],
            }

        def workflow_payload(incident: dict[str, object]) -> dict[str, object]:
            ticket = incident_state["ticket"]
            return {
                "incident": incident,
                "tickets": [ticket] if ticket else [],
                "current_ticket": ticket,
            }

        def sync_ticket(
            incident: dict[str, object],
            provider_name: str,
            note: str = "",
            force: bool = False,
            source_url: str = "",
        ) -> dict[str, object]:
            incident_state["ticket"] = sync_result
            incident_state["sync_call"] = {
                "status": incident["status"],
                "provider": provider_name,
                "note": note,
                "force": force,
                "source_url": source_url,
            }
            return sync_result

        auth = SimpleNamespace(subject="demo-operator")
        payload = control_plane_app.RemediationActionRequest(
            remediation_id=7,
            approved_by="demo-operator",
            notes="Need human coordination.",
            execute=True,
            source_url="https://demo-ui.example.com/incidents/inc-123",
        )

        with (
            mock.patch.object(control_plane_app, "ensure_role"),
            mock.patch.object(control_plane_app, "ensure_project_access"),
            mock.patch.object(control_plane_app, "get_incident", side_effect=get_incident),
            mock.patch.object(control_plane_app, "get_incident_remediation", return_value=remediation),
            mock.patch.object(control_plane_app, "_transition_incident_with_audit", side_effect=transition),
            mock.patch.object(control_plane_app, "record_approval", return_value={"id": 301}),
            mock.patch.object(control_plane_app, "record_incident_action", side_effect=record_incident_action),
            mock.patch.object(control_plane_app, "record_audit"),
            mock.patch.object(control_plane_app, "_sync_ticket_provider", side_effect=sync_ticket),
            mock.patch.object(control_plane_app, "_sync_current_ticket_best_effort") as sync_current_ticket,
            mock.patch.object(control_plane_app, "list_incidents", return_value=[]),
            mock.patch.object(control_plane_app, "set_active_incidents"),
            mock.patch.object(control_plane_app, "_workflow_payload", side_effect=workflow_payload),
        ):
            response = control_plane_app._execute_incident_action("inc-123", payload, auth=auth)

        self.assertEqual(incident_state["incident"]["status"], control_plane_app.ESCALATED)
        self.assertEqual(response["action"]["execution_status"], "executed")
        self.assertEqual(response["workflow"]["current_ticket"]["external_key"], "PLANE-42")
        self.assertIn("Plane ticket PLANE-42", response["action"]["result_summary"])
        self.assertEqual(incident_state["sync_call"]["provider"], "plane")
        self.assertEqual(incident_state["sync_call"]["status"], control_plane_app.ESCALATED)
        self.assertTrue(incident_state["sync_call"]["force"])
        self.assertEqual(
            incident_state["sync_call"]["source_url"],
            "https://demo-ui.example.com/incidents/inc-123",
        )
        sync_current_ticket.assert_not_called()


class IncidentAutoRcaPolicyTests(unittest.TestCase):
    def _incident_payload(self, **overrides: object) -> control_plane_app.IncidentCreate:
        values = {
            "incident_id": "inc-rca-1",
            "project": "ims-demo",
            "anomaly_score": 0.98,
            "anomaly_type": "registration_storm",
            "predicted_confidence": 0.94,
            "model_version": "ims-predictive-fs",
            "feature_snapshot": {"scenario_name": "registration_storm"},
        }
        values.update(overrides)
        return control_plane_app.IncidentCreate(**values)

    def _stored_incident(self) -> dict[str, object]:
        return {
            "id": "inc-rca-1",
            "project": "ims-demo",
            "status": control_plane_app.NEW,
            "anomaly_type": "registration_storm",
            "source_system": "anomaly-service",
        }

    def test_post_incident_defers_auto_rca_for_sampled_holdout(self) -> None:
        payload = self._incident_payload()
        background_tasks = _TaskRecorder()

        with (
            mock.patch.dict(control_plane_app.os.environ, {"INCIDENT_AUTO_RCA_SAMPLE_RATE": "0.9"}, clear=False),
            mock.patch.object(control_plane_app, "ensure_project_access"),
            mock.patch.object(control_plane_app, "create_incident", return_value=self._stored_incident()) as create_incident,
            mock.patch.object(control_plane_app, "_publish_incident_evidence_record"),
            mock.patch.object(control_plane_app, "_record_debug_trace_packets"),
            mock.patch.object(control_plane_app, "record_incident"),
            mock.patch.object(control_plane_app, "set_active_incidents"),
            mock.patch.object(control_plane_app, "list_incidents", return_value=[]),
            mock.patch.object(control_plane_app, "_stable_sample_ratio", return_value=0.95),
            mock.patch.object(control_plane_app, "record_audit") as record_audit,
        ):
            response = control_plane_app.post_incident(payload, background_tasks, auth=None)

        self.assertEqual(response["status"], control_plane_app.NEW)
        self.assertEqual(create_incident.call_args.args[0]["status"], control_plane_app.NEW)
        self.assertNotIn("auto_generate_rca", create_incident.call_args.args[0])
        deferred_audit = next(call for call in record_audit.call_args_list if call.args[0] == "rca_auto_generation_deferred")
        self.assertEqual(deferred_audit.kwargs["incident_id"], "inc-rca-1")
        self.assertEqual(deferred_audit.args[2]["sample_rate"], 0.9)
        self.assertEqual(deferred_audit.args[2]["sample_value"], 0.95)
        scheduled_funcs = [task[0] for task in background_tasks.tasks]
        self.assertIn(control_plane_app._publish_eda_event_best_effort, scheduled_funcs)
        self.assertNotIn(control_plane_app._auto_generate_incident_rca, scheduled_funcs)

    def test_post_incident_honors_explicit_auto_rca_override(self) -> None:
        payload = self._incident_payload(auto_generate_rca=True)
        background_tasks = _TaskRecorder()

        with (
            mock.patch.dict(control_plane_app.os.environ, {"INCIDENT_AUTO_RCA_SAMPLE_RATE": "0.0"}, clear=False),
            mock.patch.object(control_plane_app, "ensure_project_access"),
            mock.patch.object(control_plane_app, "create_incident", return_value=self._stored_incident()),
            mock.patch.object(control_plane_app, "_publish_incident_evidence_record"),
            mock.patch.object(control_plane_app, "_record_debug_trace_packets"),
            mock.patch.object(control_plane_app, "record_incident"),
            mock.patch.object(control_plane_app, "set_active_incidents"),
            mock.patch.object(control_plane_app, "list_incidents", return_value=[]),
            mock.patch.object(control_plane_app, "record_audit") as record_audit,
        ):
            control_plane_app.post_incident(payload, background_tasks, auth=None)

        scheduled_funcs = [task[0] for task in background_tasks.tasks]
        self.assertIn(control_plane_app._auto_generate_incident_rca, scheduled_funcs)
        self.assertFalse(any(call.args[0] == "rca_auto_generation_deferred" for call in record_audit.call_args_list))


class AiPlaybookGenerationTests(unittest.TestCase):
    def test_request_ai_playbook_generation_publishes_instruction_and_persists_metadata(self) -> None:
        incident = {
            "id": "inc-ai-1",
            "project": "ims-demo",
            "status": control_plane_app.REMEDIATION_SUGGESTED,
            "workflow_revision": 3,
            "anomaly_type": "registration_storm",
            "severity": "Critical",
            "feature_snapshot": {"register_rate": 1480},
            "rca_payload": {"root_cause": "S-CSCF overload", "recommendation": "Scale the S-CSCF path"},
        }
        remediation = {
            "id": 17,
            "action_ref": control_plane_app.AI_PLAYBOOK_GENERATION_ACTION,
            "title": "Generate AI Ansible playbook with watsonx",
            "description": "Ask watsonx to draft a playbook.",
            "status": "available",
            "based_on_revision": 3,
            "metadata": {"generation_kind": "request", "generation_status": "not_requested"},
        }
        captured: dict[str, object] = {}

        def update_remediation(incident_id: str, remediation_id: int, **kwargs: object) -> dict[str, object]:
            captured["incident_id"] = incident_id
            captured["remediation_id"] = remediation_id
            captured["metadata"] = kwargs["metadata"]
            return remediation | {
                "metadata": kwargs["metadata"],
                "generation_status": str((kwargs["metadata"] or {}).get("generation_status") or ""),
                "generation_provider": str((kwargs["metadata"] or {}).get("generation_provider") or ""),
            }

        with (
            mock.patch.object(control_plane_app.uuid, "uuid4", return_value=SimpleNamespace(hex="corr-123")),
            mock.patch.object(control_plane_app, "_build_playbook_generation_instruction", return_value="generate this playbook"),
            mock.patch.object(
                control_plane_app,
                "_publish_playbook_generation_instruction",
                return_value={
                    "topic": control_plane_app.AI_PLAYBOOK_GENERATION_TOPIC,
                    "correlation_id": "corr-123",
                    "bootstrap_servers": ["kafka:9092"],
                    "instruction_preview": "generate this playbook",
                },
            ),
            mock.patch.object(control_plane_app, "update_incident_remediation", side_effect=update_remediation),
            mock.patch.object(control_plane_app, "record_audit") as record_audit,
        ):
            result = control_plane_app._request_ai_playbook_generation(
                incident,
                remediation,
                "demo-operator",
                "Prefer a low-risk ingress guardrail first.",
                "https://demo-ui.example.com/incidents/inc-ai-1",
            )

        metadata = captured["metadata"]
        assert isinstance(metadata, dict)
        self.assertEqual(captured["incident_id"], "inc-ai-1")
        self.assertEqual(captured["remediation_id"], 17)
        self.assertEqual(metadata["generation_status"], "requested")
        self.assertEqual(metadata["generation_correlation_id"], "corr-123")
        self.assertEqual(metadata["generation_topic"], control_plane_app.AI_PLAYBOOK_GENERATION_TOPIC)
        self.assertEqual(metadata["generation_requested_by"], "demo-operator")
        self.assertEqual(result["remediation"]["generation_provider"], "watsonx")
        record_audit.assert_called_once()

    def test_callback_promotes_request_into_ai_generated_playbook(self) -> None:
        incident = {
            "id": "inc-ai-1",
            "project": "ims-demo",
            "status": control_plane_app.REMEDIATION_SUGGESTED,
            "workflow_revision": 5,
        }
        remediation = {
            "id": 17,
            "action_ref": control_plane_app.AI_PLAYBOOK_GENERATION_ACTION,
            "title": "Generate AI Ansible playbook with watsonx",
            "description": "Ask watsonx to draft a playbook.",
            "status": "available",
            "risk_level": "low",
            "confidence": 0.42,
            "expected_outcome": "A reviewable AI-generated playbook is attached.",
            "preconditions": ["RCA is attached"],
            "based_on_revision": 4,
            "metadata": {
                "ai_generated": True,
                "generation_kind": "request",
                "generation_status": "requested",
                "generation_correlation_id": "corr-123",
            },
        }
        captured: dict[str, object] = {}

        def update_remediation(incident_id: str, remediation_id: int, **kwargs: object) -> dict[str, object]:
            captured["incident_id"] = incident_id
            captured["remediation_id"] = remediation_id
            captured.update(kwargs)
            return remediation | {
                "title": kwargs["title"],
                "suggestion_type": kwargs["suggestion_type"],
                "action_ref": kwargs["action_ref"],
                "playbook_ref": kwargs["playbook_ref"],
                "playbook_yaml": kwargs["playbook_yaml"],
                "metadata": kwargs["metadata"],
                "generation_status": str((kwargs["metadata"] or {}).get("generation_status") or ""),
            }

        payload = control_plane_app.PlaybookGenerationCallbackRequest(
            correlation_id="corr-123",
            status="generated",
            title="Apply AI-generated registration storm guardrail",
            description="Throttle ingress retries and preserve downstream stability.",
            expected_outcome="Retry traffic slows and registrations stabilize.",
            preconditions=["Review ingress namespace", "Confirm rollback note"],
            playbook_yaml="---\n- hosts: localhost\n  gather_facts: false\n  tasks: []\n",
        )

        with (
            mock.patch.object(control_plane_app, "get_incident", return_value=incident),
            mock.patch.object(control_plane_app, "_find_ai_playbook_generation_remediation", return_value=remediation),
            mock.patch.object(control_plane_app, "update_incident_remediation", side_effect=update_remediation),
            mock.patch.object(control_plane_app, "record_audit") as record_audit,
        ):
            updated = control_plane_app._apply_ai_playbook_generation_callback("inc-ai-1", payload)

        self.assertEqual(captured["incident_id"], "inc-ai-1")
        self.assertEqual(captured["remediation_id"], 17)
        self.assertEqual(captured["suggestion_type"], "ansible_playbook")
        self.assertEqual(captured["action_ref"], "ai_generated_playbook_corr-123")
        self.assertEqual(captured["playbook_ref"], "ai_generated_playbook_corr-123")
        self.assertTrue(captured["requires_approval"])
        self.assertEqual(captured["playbook_yaml"], payload.playbook_yaml.strip())
        self.assertEqual(updated["generation_status"], "generated")
        record_audit.assert_called_once()

    def test_execute_generated_playbook_uses_dynamic_yaml(self) -> None:
        incident_state = {
            "incident": {
                "id": "inc-ai-2",
                "project": "ims-demo",
                "status": control_plane_app.REMEDIATION_SUGGESTED,
                "workflow_revision": 1,
            }
        }
        remediation = {
            "id": 21,
            "action_ref": "ai_generated_playbook_corr123",
            "playbook_ref": "ai_generated_playbook_corr123",
            "playbook_yaml": "---\n- hosts: localhost\n  gather_facts: false\n  tasks: []\n",
            "title": "AI-generated retry guardrail",
            "status": "available",
            "metadata": {"ai_generated": True, "generation_kind": "generated", "generation_status": "generated"},
        }
        captured: dict[str, object] = {}

        def get_incident(_: str) -> dict[str, object]:
            return dict(incident_state["incident"])

        def transition(incident: dict[str, object], target_state: str, _: str, __: str) -> dict[str, object]:
            updated = dict(incident)
            updated["status"] = target_state
            updated["workflow_revision"] = int(updated.get("workflow_revision") or 1) + 1
            incident_state["incident"] = updated
            return updated

        def record_incident_action(**kwargs: object) -> dict[str, object]:
            captured["action_mode"] = kwargs["action_mode"]
            return {
                "id": 901,
                "execution_status": kwargs["execution_status"],
                "result_summary": kwargs["result_summary"],
            }

        auth = SimpleNamespace(subject="demo-operator")
        payload = control_plane_app.RemediationActionRequest(
            remediation_id=21,
            approved_by="demo-operator",
            notes="Run the generated guardrail.",
            execute=True,
        )

        with (
            mock.patch.object(control_plane_app, "ensure_role"),
            mock.patch.object(control_plane_app, "ensure_project_access"),
            mock.patch.object(control_plane_app, "get_incident", side_effect=get_incident),
            mock.patch.object(control_plane_app, "get_incident_remediation", return_value=remediation),
            mock.patch.object(control_plane_app, "_transition_incident_with_audit", side_effect=transition),
            mock.patch.object(control_plane_app, "record_approval", return_value={"id": 404}),
            mock.patch.object(control_plane_app, "record_incident_action", side_effect=record_incident_action),
            mock.patch.object(control_plane_app, "record_audit"),
            mock.patch.object(control_plane_app, "_sync_current_ticket_best_effort"),
            mock.patch.object(control_plane_app, "list_incidents", return_value=[]),
            mock.patch.object(control_plane_app, "set_active_incidents"),
            mock.patch.object(control_plane_app, "_workflow_payload", return_value={"incident": incident_state["incident"]}),
            mock.patch.object(control_plane_app, "_execute_playbook", return_value=("simulated", "simulated")) as execute_playbook,
        ):
            response = control_plane_app._execute_incident_action("inc-ai-2", payload, auth=auth)

        self.assertEqual(response["action"]["execution_status"], "executed")
        self.assertEqual(captured["action_mode"], "ansible")
        self.assertEqual(incident_state["incident"]["status"], control_plane_app.EXECUTED)
        execute_playbook.assert_called_once()
        self.assertEqual(execute_playbook.call_args.kwargs["playbook_content"], remediation["playbook_yaml"].strip())


if __name__ == "__main__":
    unittest.main()
