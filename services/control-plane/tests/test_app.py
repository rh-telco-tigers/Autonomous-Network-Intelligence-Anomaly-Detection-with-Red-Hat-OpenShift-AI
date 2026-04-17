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
                "project": "ani-demo",
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

        def workflow_payload(incident: dict[str, object]) -> dict[str, object]:
            return {
                "incident": incident,
                "available_transitions": sorted(
                    control_plane_app._available_transition_targets(str(incident.get("status") or control_plane_app.NEW))
                ),
            }

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


class VerificationWorkflowTests(unittest.TestCase):
    def test_verify_incident_allows_escalated_to_verification_failed(self) -> None:
        incident_state = {
            "incident": {
                "id": "inc-verify-1",
                "project": "ani-demo",
                "status": control_plane_app.ESCALATED,
                "workflow_revision": 4,
            }
        }

        def get_incident(_: str) -> dict[str, object]:
            return dict(incident_state["incident"])

        def transition(incident: dict[str, object], target_state: str, _: str, __: str) -> dict[str, object]:
            updated = dict(incident)
            updated["status"] = target_state
            updated["workflow_revision"] = int(updated.get("workflow_revision") or 1) + 1
            incident_state["incident"] = updated
            return updated

        def workflow_payload(incident: dict[str, object]) -> dict[str, object]:
            return {
                "incident": incident,
                "available_transitions": sorted(
                    control_plane_app._available_transition_targets(str(incident.get("status") or control_plane_app.NEW))
                ),
            }

        payload = control_plane_app.VerificationRequest(
            verification_status="failed",
            verified_by="demo-operator",
            notes="Playbook run failed and needs follow-up.",
        )

        with (
            mock.patch.object(control_plane_app, "ensure_role"),
            mock.patch.object(control_plane_app, "ensure_project_access"),
            mock.patch.object(control_plane_app, "get_incident", side_effect=get_incident),
            mock.patch.object(control_plane_app, "record_verification", return_value={"id": 901, "verification_status": "failed"}),
            mock.patch.object(control_plane_app, "_transition_incident_with_audit", side_effect=transition),
            mock.patch.object(control_plane_app, "_maybe_create_resolution_extract", return_value=None),
            mock.patch.object(control_plane_app, "record_audit"),
            mock.patch.object(control_plane_app, "_sync_current_ticket_best_effort"),
            mock.patch.object(control_plane_app, "_workflow_payload", side_effect=workflow_payload),
            mock.patch.object(control_plane_app, "list_incident_tickets", return_value=[]),
            mock.patch.object(control_plane_app, "set_active_incidents"),
            mock.patch.object(control_plane_app, "list_incidents", return_value=[]),
            mock.patch.object(control_plane_app, "record_verification_metric"),
        ):
            response = control_plane_app.verify_incident("inc-verify-1", payload, auth=None)

        self.assertEqual(incident_state["incident"]["status"], control_plane_app.VERIFICATION_FAILED)
        self.assertEqual(response["workflow"]["incident"]["status"], control_plane_app.VERIFICATION_FAILED)
        self.assertIn(
            control_plane_app.ESCALATED,
            response["workflow"]["available_transitions"],
        )


class IncidentAutoRcaPolicyTests(unittest.TestCase):
    def _incident_payload(self, **overrides: object) -> control_plane_app.IncidentCreate:
        values = {
            "incident_id": "inc-rca-1",
            "project": "ani-demo",
            "anomaly_score": 0.98,
            "anomaly_type": "registration_storm",
            "predicted_confidence": 0.94,
            "model_version": "ani-predictive-fs",
            "feature_snapshot": {"scenario_name": "registration_storm"},
        }
        values.update(overrides)
        return control_plane_app.IncidentCreate(**values)

    def _stored_incident(self) -> dict[str, object]:
        return {
            "id": "inc-rca-1",
            "project": "ani-demo",
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


class ConsoleScenarioFallbackTests(unittest.TestCase):
    def test_console_run_scenario_forces_incident_when_non_nominal_score_returns_no_incident(self) -> None:
        payload = control_plane_app.ConsoleScenarioRequest(scenario="malformed_invite", project="ani-demo")
        feature_window = {
            "window_id": "win-1",
            "scenario_name": "malformed_invite",
            "feature_source": "sipp-window-template",
            "source": "feature-gateway-console",
            "anomaly_type": "malformed_sip",
            "labels": {"anomaly_type": "malformed_sip"},
            "features": {
                "invite_rate": 1.0,
                "payload_variance": 120.0,
            },
        }
        score = {
            "anomaly_score": 0.77,
            "is_anomaly": False,
            "incident_id": None,
            "anomaly_type": control_plane_app.NORMAL_ANOMALY_TYPE,
            "predicted_anomaly_type": control_plane_app.NORMAL_ANOMALY_TYPE,
            "predicted_confidence": 0.22,
            "class_probabilities": {"malformed_sip": 0.11, control_plane_app.NORMAL_ANOMALY_TYPE: 0.22},
            "top_classes": [{"anomaly_type": control_plane_app.NORMAL_ANOMALY_TYPE, "probability": 0.22}],
            "model_version": "ani-predictive-backfill",
            "scoring_mode": "remote-kserve:backfill",
        }
        fallback_incident = {
            "id": "inc-fallback-1",
            "project": "ani-demo",
            "status": control_plane_app.AWAITING_APPROVAL,
            "workflow_state": control_plane_app.AWAITING_APPROVAL,
            "rca_payload": {"root_cause": "Malformed payload"},
        }
        state = {"incidents": [fallback_incident]}

        with (
            mock.patch.object(control_plane_app, "ensure_project_access"),
            mock.patch.object(control_plane_app, "_request_json", side_effect=[feature_window, score]),
            mock.patch.object(control_plane_app, "_force_console_scenario_incident", return_value=fallback_incident) as force_incident,
            mock.patch.object(control_plane_app, "_record_debug_trace_packets"),
            mock.patch.object(control_plane_app, "record_audit"),
            mock.patch.object(control_plane_app, "_wait_for_incident_rca", return_value=fallback_incident),
            mock.patch.object(control_plane_app, "_build_console_state", return_value=state),
        ):
            response = control_plane_app.console_run_scenario(payload, auth=None)

        self.assertEqual(response["incident"]["id"], "inc-fallback-1")
        self.assertTrue(response["score"]["is_anomaly"])
        self.assertEqual(response["score"]["anomaly_type"], "malformed_sip")
        self.assertEqual(response["score"]["predicted_anomaly_type"], "malformed_sip")
        force_incident.assert_called_once()


class AIPlaybookGenerationRetryTests(unittest.TestCase):
    def test_request_ai_playbook_generation_schedules_retry_publish(self) -> None:
        incident = {
            "id": "inc-retry-1",
            "project": "ani-demo",
            "rca_payload": {"root_cause": "worker saturation"},
        }
        remediation = {
            "id": 17,
            "action_ref": "generate_ai_ansible_playbook",
            "metadata": {},
        }
        background_tasks = _TaskRecorder()

        with (
            mock.patch.object(control_plane_app, "_ai_playbook_generation_enabled", return_value=True),
            mock.patch.object(control_plane_app, "_is_ai_playbook_generation_request", return_value=True),
            mock.patch.object(control_plane_app, "_build_playbook_generation_instruction", return_value="instruction"),
            mock.patch.object(
                control_plane_app,
                "_publish_playbook_generation_instruction",
                return_value={"topic": "aiops-ansible-playbook-generate-instruction"},
            ),
            mock.patch.object(control_plane_app, "update_incident_remediation", return_value={"id": 17}),
            mock.patch.object(control_plane_app, "record_audit"),
        ):
            control_plane_app._request_ai_playbook_generation(
                incident,
                remediation,
                "demo-operator",
                "retry if missing",
                "",
                background_tasks=background_tasks,
            )

        self.assertEqual(len(background_tasks.tasks), 1)
        self.assertIs(background_tasks.tasks[0][0], control_plane_app._retry_ai_playbook_generation_publish)
        task_args = background_tasks.tasks[0][1]
        self.assertEqual(task_args[0], "inc-retry-1")
        self.assertEqual(task_args[1], 17)
        self.assertIsInstance(task_args[2], str)
        self.assertTrue(task_args[2])
        self.assertEqual(task_args[3], "instruction")

    def test_retry_ai_playbook_generation_republishes_pending_request(self) -> None:
        remediation = {
            "id": 17,
            "metadata": {
                "generation_correlation_id": "corr-123",
                "generation_status": "requested",
            },
        }

        with (
            mock.patch.object(control_plane_app.time, "sleep"),
            mock.patch.object(control_plane_app, "get_incident_remediation", return_value=remediation),
            mock.patch.object(
                control_plane_app,
                "_publish_playbook_generation_instruction",
                return_value={"topic": "aiops-ansible-playbook-generate-instruction"},
            ) as publish,
            mock.patch.object(control_plane_app, "update_incident_remediation", return_value={"id": 17}) as update,
            mock.patch.object(control_plane_app, "record_audit"),
        ):
            control_plane_app._retry_ai_playbook_generation_publish(
                "inc-retry-1",
                17,
                "corr-123",
                "instruction",
            )

        publish.assert_called_once_with("corr-123", "instruction")
        update.assert_called_once()


class IncidentEvidenceSourceTests(unittest.TestCase):
    def test_evidence_sources_tolerate_non_numeric_document_scores(self) -> None:
        incident = {
            "rca_payload": {
                "retrieved_documents": [
                    {
                        "title": "Server internal error RCA",
                        "reference": "runbooks/server-internal-error",
                        "collection": "ani_runbooks",
                        "doc_type": "runbook",
                        "score": None,
                        "excerpt": "One service cohort is returning 5xx.",
                    },
                    {
                        "title": "Worker saturation query",
                        "reference": "queries/worker-saturation",
                        "collection": "ani_queries",
                        "doc_type": "query",
                        "score": "not-a-number",
                        "excerpt": "Queue depth is rising.",
                    },
                ]
            }
        }

        evidence = control_plane_app._evidence_sources(incident)

        self.assertEqual(len(evidence), 2)
        self.assertEqual(evidence[0]["score"], 0.0)
        self.assertEqual(evidence[1]["score"], 0.0)
        self.assertIn("score 0.00", evidence[0]["detail"])
        self.assertIn("score 0.00", evidence[1]["detail"])


class AiPlaybookGenerationTests(unittest.TestCase):
    def test_preview_ai_playbook_generation_instruction_uses_draft_correlation(self) -> None:
        incident = {
            "id": "inc-ai-1",
            "project": "ani-demo",
            "rca_payload": {"root_cause": "S-CSCF overload"},
        }
        remediation = {
            "id": 17,
            "action_ref": control_plane_app.AI_PLAYBOOK_GENERATION_ACTION,
            "metadata": {"generation_kind": "request", "generation_status": "not_requested"},
        }

        with mock.patch.object(control_plane_app, "_build_playbook_generation_instruction", return_value="draft instruction") as build:
            preview = control_plane_app._preview_ai_playbook_generation_instruction(
                incident,
                remediation,
                "Prefer a low-risk ingress guardrail first.",
                "https://demo-ui.example.com/incidents/inc-ai-1",
            )

        build.assert_called_once_with(
            incident,
            remediation,
            control_plane_app.AI_PLAYBOOK_GENERATION_PREVIEW_CORRELATION_ID,
            "Prefer a low-risk ingress guardrail first.",
            "https://demo-ui.example.com/incidents/inc-ai-1",
        )
        self.assertEqual(preview["instruction"], "draft instruction")
        self.assertEqual(preview["correlation_id"], control_plane_app.AI_PLAYBOOK_GENERATION_PREVIEW_CORRELATION_ID)
        self.assertTrue(preview["draft"])

    def test_request_ai_playbook_generation_uses_instruction_override_when_provided(self) -> None:
        incident = {
            "id": "inc-ai-1",
            "project": "ani-demo",
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
            "title": "Generate AI Ansible playbook",
            "description": "Ask the external generator to draft a playbook.",
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
            mock.patch.object(control_plane_app.uuid, "uuid4", return_value=SimpleNamespace(hex="corr-456")),
            mock.patch.object(control_plane_app, "_build_playbook_generation_instruction") as build_instruction,
            mock.patch.object(
                control_plane_app,
                "_publish_playbook_generation_instruction",
                return_value={
                    "topic": control_plane_app.AI_PLAYBOOK_GENERATION_TOPIC,
                    "correlation_id": "corr-456",
                    "bootstrap_servers": ["kafka:9092"],
                    "instruction": "Use this exact draft",
                    "instruction_preview": "Use this exact draft",
                },
            ) as publish_instruction,
            mock.patch.object(control_plane_app, "update_incident_remediation", side_effect=update_remediation),
            mock.patch.object(control_plane_app, "record_audit"),
        ):
            control_plane_app._request_ai_playbook_generation(
                incident,
                remediation,
                "demo-operator",
                "Prefer a low-risk ingress guardrail first.",
                "https://demo-ui.example.com/incidents/inc-ai-1",
                "Use this exact draft",
            )

        build_instruction.assert_not_called()
        publish_instruction.assert_called_once_with("corr-456", "Use this exact draft")
        metadata = captured["metadata"]
        assert isinstance(metadata, dict)
        self.assertEqual(metadata["generation_instruction"], "Use this exact draft")

    def test_request_ai_playbook_generation_publishes_instruction_and_persists_metadata(self) -> None:
        incident = {
            "id": "inc-ai-1",
            "project": "ani-demo",
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
            "title": "Generate AI Ansible playbook",
            "description": "Ask the external generator to draft a playbook.",
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
                    "instruction": "generate this playbook",
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
        self.assertEqual(result["remediation"]["generation_provider"], control_plane_app.AI_PLAYBOOK_GENERATION_PROVIDER)
        record_audit.assert_called_once()

    def test_callback_promotes_request_into_ai_generated_playbook(self) -> None:
        incident = {
            "id": "inc-ai-1",
            "project": "ani-demo",
            "status": control_plane_app.REMEDIATION_SUGGESTED,
            "workflow_revision": 5,
        }
        remediation = {
            "id": 17,
            "action_ref": control_plane_app.AI_PLAYBOOK_GENERATION_ACTION,
            "title": "Generate AI Ansible playbook",
            "description": "Ask the external generator to draft a playbook.",
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
            mock.patch.object(
                control_plane_app,
                "_sync_ai_generated_playbook_to_gitea",
                return_value={
                    "gitea_repo_owner": "gitadmin",
                    "gitea_repo_name": "ani-ai-generated-playbooks",
                    "gitea_draft_branch": "draft/inc-ai-1",
                    "gitea_main_branch": "main",
                    "gitea_playbook_path": "playbooks/inc-ai-1/playbook.yaml",
                    "gitea_draft_commit_sha": "abc123",
                    "gitea_sync_status": "drafted",
                },
            ),
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
        self.assertEqual(captured["metadata"]["gitea_draft_branch"], "draft/inc-ai-1")
        self.assertEqual(updated["generation_status"], "generated")
        record_audit.assert_called_once()

    def test_callback_normalizes_generated_playbook_to_supported_rate_limit_template(self) -> None:
        incident = {
            "id": "inc-ai-ops-1",
            "project": "ani-demo",
            "status": control_plane_app.REMEDIATION_SUGGESTED,
            "workflow_revision": 6,
            "anomaly_type": "registration_storm",
            "recommendation": "Rate limit the P-CSCF ingress path",
        }
        remediation = {
            "id": 18,
            "action_ref": control_plane_app.AI_PLAYBOOK_GENERATION_ACTION,
            "title": "Generate AI Ansible playbook",
            "description": "Ask the external generator to draft a playbook.",
            "status": "available",
            "risk_level": "low",
            "confidence": 0.42,
            "expected_outcome": "A reviewable AI-generated playbook is attached.",
            "preconditions": ["RCA is attached"],
            "based_on_revision": 6,
            "metadata": {
                "ai_generated": True,
                "generation_kind": "request",
                "generation_status": "requested",
                "generation_correlation_id": "corr-rate-limit",
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
            correlation_id="corr-rate-limit",
            status="generated",
            title="Rate limit P-CSCF ingress path",
            description="Apply a safe P-CSCF ingress guardrail for retry amplification.",
            action_ref="RateLimitP-CSCFIngress",
            playbook_ref="IMS-Retry-Amplification-Mitigation",
            playbook_yaml=(
                "---\n"
                "- name: invalid generic guardrail\n"
                "  hosts: localhost\n"
                "  gather_facts: false\n"
                "  tasks:\n"
                "    - name: unsupported generic patch\n"
                "      k8s:\n"
                "        state: present\n"
            ),
        )

        with (
            mock.patch.dict(
                control_plane_app.os.environ,
                {
                    "AAP_RATE_LIMIT_PCSCF_NAMESPACE": "ani-sipp",
                    "AAP_RATE_LIMIT_PCSCF_DEPLOYMENT": "ims-pcscf",
                    "AAP_RATE_LIMIT_PCSCF_ANNOTATION_KEY": "ani.demo/rate-limit-review",
                    "AAP_RATE_LIMIT_PCSCF_ANNOTATION_VALUE": "eda-guardrail",
                },
                clear=False,
            ),
            mock.patch.object(control_plane_app, "get_incident", return_value=incident),
            mock.patch.object(control_plane_app, "_find_ai_playbook_generation_remediation", return_value=remediation),
            mock.patch.object(
                control_plane_app,
                "_sync_ai_generated_playbook_to_gitea",
                return_value={
                    "gitea_repo_owner": "gitadmin",
                    "gitea_repo_name": "ani-ai-generated-playbooks",
                    "gitea_draft_branch": "draft/inc-ai-ops-1",
                    "gitea_main_branch": "main",
                    "gitea_playbook_path": "playbooks/inc-ai-ops-1/playbook.yaml",
                    "gitea_draft_commit_sha": "rate123",
                    "gitea_sync_status": "drafted",
                },
            ),
            mock.patch.object(control_plane_app, "update_incident_remediation", side_effect=update_remediation),
            mock.patch.object(control_plane_app, "record_audit"),
        ):
            updated = control_plane_app._apply_ai_playbook_generation_callback("inc-ai-ops-1", payload)

        normalized_yaml = str(captured["playbook_yaml"])
        self.assertEqual(captured["action_ref"], "RateLimitP-CSCFIngress")
        self.assertEqual(captured["playbook_ref"], "IMS-Retry-Amplification-Mitigation")
        self.assertIn("ansible.builtin.uri:", normalized_yaml)
        self.assertIn("target_namespace | default('ani-sipp')", normalized_yaml)
        self.assertIn("target_deployment | default('ims-pcscf')", normalized_yaml)
        self.assertIn("ani.demo/rate-limit-review", normalized_yaml)
        self.assertNotIn("\n      k8s:\n", normalized_yaml)
        self.assertEqual(captured["metadata"]["supported_action_ref"], "rate_limit_pcscf")
        self.assertTrue(captured["metadata"]["environment_normalized"])
        self.assertEqual(updated["generation_status"], "generated")

    def test_failed_callback_falls_back_to_supported_rate_limit_template(self) -> None:
        incident = {
            "id": "inc-ai-ops-parse-1",
            "project": "ani-demo",
            "status": control_plane_app.REMEDIATION_SUGGESTED,
            "workflow_revision": 6,
            "anomaly_type": "registration_storm",
            "recommendation": "Rate limit the P-CSCF ingress path",
            "rca_payload": {
                "recommendation": "Rate limit the P-CSCF ingress path",
                "root_cause": "Retry amplification saturating the P-CSCF ingress path",
            },
        }
        remediation = {
            "id": 19,
            "action_ref": control_plane_app.AI_PLAYBOOK_GENERATION_ACTION,
            "title": "Generate AI Ansible playbook",
            "description": "Ask the external generator to draft a playbook.",
            "status": "available",
            "risk_level": "low",
            "confidence": 0.42,
            "expected_outcome": "A reviewable AI-generated playbook is attached.",
            "preconditions": ["RCA is attached"],
            "based_on_revision": 6,
            "metadata": {
                "ai_generated": True,
                "generation_kind": "request",
                "generation_status": "requested",
                "generation_correlation_id": "corr-rate-limit-failed",
            },
        }
        supported_remediation = {
            "id": 7,
            "action_ref": "rate_limit_pcscf",
            "playbook_ref": "RateLimitPcscfIngress",
            "title": "Rate limit the P-CSCF ingress path",
            "description": "Apply a safe P-CSCF ingress guardrail for retry amplification.",
            "expected_outcome": "Retry traffic slows and registrations stabilize.",
            "preconditions": ["Review ingress namespace", "Confirm rollback note"],
            "risk_level": "low",
            "confidence": 0.91,
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
            correlation_id="corr-rate-limit-failed",
            status="failed",
            provider_name="Ansible Lightspeed",
            error=(
                "Failed to parse generated playbook YAML: while parsing a block collection\n"
                "  in \"<unicode string>\", line 28, column 19:\n"
                "                      - podSelector:\n"
                "                      ^\n"
                "expected <block end>, but found '?'\n"
                "  in \"<unicode string>\", line 31, column 19:\n"
                "                      ports:\n"
                "                      ^"
            ),
        )

        with (
            mock.patch.dict(
                control_plane_app.os.environ,
                {
                    "AAP_RATE_LIMIT_PCSCF_NAMESPACE": "ani-sipp",
                    "AAP_RATE_LIMIT_PCSCF_DEPLOYMENT": "ims-pcscf",
                    "AAP_RATE_LIMIT_PCSCF_ANNOTATION_KEY": "ani.demo/rate-limit-review",
                    "AAP_RATE_LIMIT_PCSCF_ANNOTATION_VALUE": "eda-guardrail",
                },
                clear=False,
            ),
            mock.patch.object(control_plane_app, "get_incident", return_value=incident),
            mock.patch.object(control_plane_app, "_find_ai_playbook_generation_remediation", return_value=remediation),
            mock.patch.object(control_plane_app, "_find_matching_remediation", return_value=supported_remediation),
            mock.patch.object(
                control_plane_app,
                "_sync_ai_generated_playbook_to_gitea",
                return_value={
                    "gitea_repo_owner": "gitadmin",
                    "gitea_repo_name": "ani-ai-generated-playbooks",
                    "gitea_draft_branch": "draft/inc-ai-ops-parse-1",
                    "gitea_main_branch": "main",
                    "gitea_playbook_path": "playbooks/inc-ai-ops-parse-1/playbook.yaml",
                    "gitea_draft_commit_sha": "parse123",
                    "gitea_sync_status": "drafted",
                },
            ),
            mock.patch.object(control_plane_app, "update_incident_remediation", side_effect=update_remediation),
            mock.patch.object(control_plane_app, "record_audit") as record_audit,
        ):
            updated = control_plane_app._apply_ai_playbook_generation_callback("inc-ai-ops-parse-1", payload)

        normalized_yaml = str(captured["playbook_yaml"])
        self.assertEqual(captured["action_ref"], "rate_limit_pcscf")
        self.assertEqual(captured["playbook_ref"], "RateLimitPcscfIngress")
        self.assertIn("ansible.builtin.uri:", normalized_yaml)
        self.assertIn("target_namespace | default('ani-sipp')", normalized_yaml)
        self.assertEqual(captured["metadata"]["supported_action_ref"], "rate_limit_pcscf")
        self.assertTrue(captured["metadata"]["supported_fallback_template"])
        self.assertEqual(captured["metadata"]["generation_fallback_reason"], "supported_template_from_failed_callback")
        self.assertEqual(updated["generation_status"], "generated")
        self.assertEqual(record_audit.call_args_list[0].args[0], "ai_playbook_generation_failed_fallback_applied")

    def test_launch_dynamic_playbook_uses_supported_action_ref_for_environment_vars(self) -> None:
        incident = {"id": "inc-ai-ops-2", "project": "ani-demo", "workflow_revision": 1}
        remediation = {
            "id": 22,
            "action_ref": "RateLimitP-CSCFIngress",
            "playbook_ref": "IMS-Retry-Amplification-Mitigation",
            "metadata": {
                "ai_generated": True,
                "generation_kind": "generated",
                "generation_status": "generated",
                "supported_action_ref": "rate_limit_pcscf",
                "gitea_draft_branch": "draft/inc-ai-ops-2",
            },
        }

        with (
            mock.patch.dict(
                control_plane_app.os.environ,
                {
                    "AAP_RATE_LIMIT_PCSCF_NAMESPACE": "ani-sipp",
                    "AAP_RATE_LIMIT_PCSCF_DEPLOYMENT": "ims-pcscf",
                    "AAP_RATE_LIMIT_PCSCF_ANNOTATION_KEY": "ani.demo/rate-limit-review",
                    "AAP_RATE_LIMIT_PCSCF_ANNOTATION_VALUE": "eda-guardrail",
                },
                clear=False,
            ),
            mock.patch.object(
                control_plane_app,
                "aap_launch_repo_playbook",
                return_value={
                    "job_id": 812,
                    "job_template_id": 73,
                    "job_template_name": "ANI AI Generated Playbook inc-ai-ops-2",
                    "job_api_url": "https://aap.example/api/v2/jobs/812/",
                    "job_stdout_url": "https://aap.example/api/v2/jobs/812/stdout/",
                    "playbook": "IMS-Retry-Amplification-Mitigation",
                    "scm_branch": "draft/inc-ai-ops-2",
                },
            ) as launch_repo_playbook,
        ):
            result = control_plane_app._launch_aap_dynamic_playbook(
                "RateLimitP-CSCFIngress",
                "---\n- hosts: localhost\n  gather_facts: false\n  tasks: []\n",
                incident,
                remediation,
                "demo-operator",
                "Review the generated draft.",
            )

        extra_vars = launch_repo_playbook.call_args.args[1]
        self.assertEqual(extra_vars["target_namespace"], "ani-sipp")
        self.assertEqual(extra_vars["target_deployment"], "ims-pcscf")
        self.assertEqual(extra_vars["annotation_key"], "ani.demo/rate-limit-review")
        self.assertEqual(extra_vars["annotation_value"], "eda-guardrail")
        self.assertEqual(extra_vars["action_ref"], "RateLimitP-CSCFIngress")
        self.assertEqual(result["scm_branch"], "draft/inc-ai-ops-2")

    def test_execute_generated_playbook_promotes_repo_and_launches_aap_controller_job(self) -> None:
        incident_state = {
            "incident": {
                "id": "inc-ai-2",
                "project": "ani-demo",
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
        remediation_state = {"remediation": dict(remediation)}
        captured: dict[str, object] = {}

        def get_incident(_: str) -> dict[str, object]:
            return dict(incident_state["incident"])

        def get_incident_remediation(_: str, __: int) -> dict[str, object]:
            return dict(remediation_state["remediation"])

        def update_remediation(_: str, __: int, **kwargs: object) -> dict[str, object]:
            updated = dict(remediation_state["remediation"])
            if "playbook_yaml" in kwargs and kwargs["playbook_yaml"] is not None:
                updated["playbook_yaml"] = str(kwargs["playbook_yaml"])
            if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
                updated["metadata"] = dict(kwargs["metadata"])
            remediation_state["remediation"] = updated
            captured["persisted_playbook_yaml"] = updated["playbook_yaml"]
            return dict(updated)

        def transition(incident: dict[str, object], target_state: str, _: str, __: str) -> dict[str, object]:
            updated = dict(incident)
            updated["status"] = target_state
            updated["workflow_revision"] = int(updated.get("workflow_revision") or 1) + 1
            incident_state["incident"] = updated
            return updated

        def record_incident_action(**kwargs: object) -> dict[str, object]:
            captured["action_mode"] = kwargs["action_mode"]
            captured["result_json"] = kwargs["result_json"]
            return {
                "id": 901,
                "execution_status": kwargs["execution_status"],
                "result_summary": kwargs["result_summary"],
            }

        auth = SimpleNamespace(subject="demo-operator")
        background_tasks = _TaskRecorder()
        payload = control_plane_app.RemediationActionRequest(
            remediation_id=21,
            approved_by="demo-operator",
            notes="Run the generated guardrail.",
            execute=True,
            playbook_yaml=(
                "---\n"
                "- hosts: localhost\n"
                "  gather_facts: false\n"
                "  tasks:\n"
                "    - name: Apply ingress safeguard annotation\n"
                "      debug:\n"
                "        msg: safeguard applied\n"
            ),
        )

        with (
            mock.patch.object(control_plane_app, "ensure_role"),
            mock.patch.object(control_plane_app, "ensure_project_access"),
            mock.patch.object(control_plane_app, "get_incident", side_effect=get_incident),
            mock.patch.object(control_plane_app, "get_incident_remediation", side_effect=get_incident_remediation),
            mock.patch.object(control_plane_app, "update_incident_remediation", side_effect=update_remediation),
            mock.patch.object(control_plane_app, "_transition_incident_with_audit", side_effect=transition),
            mock.patch.object(control_plane_app, "record_approval", return_value={"id": 404}),
            mock.patch.object(control_plane_app, "record_incident_action", side_effect=record_incident_action),
            mock.patch.object(control_plane_app, "record_audit"),
            mock.patch.object(control_plane_app, "_sync_current_ticket_best_effort") as sync_current_ticket,
            mock.patch.object(control_plane_app, "list_incidents", return_value=[]),
            mock.patch.object(control_plane_app, "set_active_incidents"),
            mock.patch.object(control_plane_app, "_workflow_payload", return_value={"incident": incident_state["incident"]}),
            mock.patch.object(
                control_plane_app,
                "_promote_ai_generated_playbook_remediation",
                side_effect=lambda incident_id, current, approved_by: dict(current)
                | {
                    "metadata": {
                        **(current.get("metadata") if isinstance(current.get("metadata"), dict) else {}),
                        "gitea_repo_owner": "gitadmin",
                        "gitea_repo_name": "ani-ai-generated-playbooks",
                        "gitea_draft_branch": "draft/inc-ai-2",
                        "gitea_main_branch": "main",
                        "gitea_playbook_path": "playbooks/inc-ai-2/playbook.yaml",
                        "gitea_pr_number": 17,
                        "gitea_pr_url": "https://gitea.example/pulls/17",
                        "gitea_merge_commit_sha": "merge123",
                    }
                },
            ) as promote_playbook,
            mock.patch.object(
                control_plane_app,
                "_launch_aap_dynamic_playbook",
                return_value={
                    "backend": "aap-controller",
                    "job_id": 812,
                    "job_template_id": 73,
                    "job_template_name": "ANI AI Generated Playbook inc-ai-2",
                    "job_api_url": "https://aap.example/api/v2/jobs/812/",
                    "job_stdout_url": "https://aap.example/api/v2/jobs/812/stdout/",
                    "playbook": "ai_generated_playbook_corr123",
                    "scm_branch": "draft/inc-ai-2",
                    "launch_summary": "Launched AAP job 812 for AI-generated playbook ai_generated_playbook_corr123 from branch draft/inc-ai-2.",
                },
            ) as launch_dynamic_playbook,
        ):
            response = control_plane_app._execute_incident_action("inc-ai-2", payload, auth=auth, background_tasks=background_tasks)

        self.assertEqual(response["action"]["execution_status"], "executing")
        self.assertEqual(captured["action_mode"], "ansible")
        self.assertEqual(incident_state["incident"]["status"], control_plane_app.EXECUTING)
        self.assertEqual(captured["persisted_playbook_yaml"], payload.playbook_yaml.strip())
        self.assertEqual(captured["result_json"]["backend"], "aap-controller")
        self.assertEqual(captured["result_json"]["gitea_pr_number"], 17)
        self.assertEqual(captured["result_json"]["scm_branch"], "draft/inc-ai-2")
        promote_playbook.assert_called_once()
        launch_dynamic_playbook.assert_called_once()
        self.assertEqual(launch_dynamic_playbook.call_args.args[1], payload.playbook_yaml.strip())
        sync_current_ticket.assert_not_called()
        self.assertEqual(len(background_tasks.tasks), 2)
        self.assertIs(background_tasks.tasks[0][0], control_plane_app._sync_current_ticket_best_effort_for_incident)
        self.assertIs(background_tasks.tasks[1][0], control_plane_app._finalize_aap_automation)

    def test_execute_generated_playbook_rejects_yaml_changes_after_approval(self) -> None:
        incident = {
            "id": "inc-ai-3",
            "project": "ani-demo",
            "status": control_plane_app.APPROVED,
            "workflow_revision": 4,
        }
        remediation = {
            "id": 33,
            "action_ref": "ai_generated_playbook_corr789",
            "playbook_ref": "ai_generated_playbook_corr789",
            "playbook_yaml": "---\n- hosts: localhost\n  gather_facts: false\n  tasks: []\n",
            "title": "AI-generated safe rollback",
            "status": "approved",
            "metadata": {"ai_generated": True, "generation_kind": "generated", "generation_status": "generated"},
        }
        auth = SimpleNamespace(subject="demo-operator")
        payload = control_plane_app.RemediationActionRequest(
            remediation_id=33,
            approved_by="demo-operator",
            notes="Execute the already approved version.",
            execute=True,
            playbook_yaml="---\n- hosts: localhost\n  gather_facts: false\n  tasks:\n    - debug:\n        msg: changed after approval\n",
        )

        with (
            mock.patch.object(control_plane_app, "ensure_role"),
            mock.patch.object(control_plane_app, "ensure_project_access"),
            mock.patch.object(control_plane_app, "get_incident", return_value=incident),
            mock.patch.object(control_plane_app, "get_incident_remediation", return_value=remediation),
        ):
            with self.assertRaises(control_plane_app.HTTPException) as raised:
                control_plane_app._execute_incident_action("inc-ai-3", payload, auth=auth, background_tasks=_TaskRecorder())

        self.assertEqual(raised.exception.status_code, 409)


class ClassifierProfileSelectionTests(unittest.TestCase):
    def test_classifier_profile_status_falls_back_to_live_when_backfill_unconfigured(self) -> None:
        with (
            mock.patch.object(
                control_plane_app,
                "classifier_profile_catalog",
                return_value={
                    "live": {
                        "key": "live",
                        "label": "Live model",
                        "description": "Live path",
                        "endpoint": "http://predictive-live.example.com",
                        "model_name": "ani-predictive-fs",
                        "model_version_label": "ani-predictive-fs",
                        "configured": True,
                    },
                    "backfill": {
                        "key": "backfill",
                        "label": "Backfill model",
                        "description": "Backfill path",
                        "endpoint": "",
                        "model_name": "ani-predictive-backfill",
                        "model_version_label": "ani-predictive-backfill",
                        "configured": False,
                    },
                },
            ),
            mock.patch.object(
                control_plane_app,
                "_probe_service",
                side_effect=[
                    {"ok": True, "status": "ready"},
                    {"ok": False, "status": "error"},
                ],
            ),
            mock.patch.object(
                control_plane_app,
                "get_app_setting_record",
                return_value={
                    "key": control_plane_app.CLASSIFIER_PROFILE_SETTING_KEY,
                    "value": {"profile": "backfill"},
                    "updated_at": "2026-04-11T00:00:00+00:00",
                },
            ),
        ):
            status = control_plane_app._classifier_profile_status()

        self.assertEqual(status["requested_profile"], "backfill")
        self.assertEqual(status["active_profile"], "live")
        self.assertTrue(next(item for item in status["profiles"] if item["key"] == "live")["active"])

    def test_set_classifier_profile_persists_requested_profile(self) -> None:
        payload = control_plane_app.ClassifierProfileSelectionRequest(profile="backfill", updated_by="demo-ui")

        with (
            mock.patch.object(control_plane_app, "ensure_role"),
            mock.patch.object(
                control_plane_app,
                "classifier_profile_catalog",
                return_value={
                    "live": {
                        "key": "live",
                        "label": "Live model",
                        "description": "Live path",
                        "endpoint": "http://predictive-live.example.com",
                        "model_name": "ani-predictive-fs",
                        "model_version_label": "ani-predictive-fs",
                        "configured": True,
                    },
                    "backfill": {
                        "key": "backfill",
                        "label": "Backfill model",
                        "description": "Backfill path",
                        "endpoint": "http://predictive-backfill.example.com",
                        "model_name": "ani-predictive-backfill",
                        "model_version_label": "ani-predictive-backfill",
                        "configured": True,
                    },
                },
            ),
            mock.patch.object(
                control_plane_app,
                "set_app_setting",
                return_value={
                    "key": control_plane_app.CLASSIFIER_PROFILE_SETTING_KEY,
                    "value": {"profile": "backfill", "updated_by": "demo-ui"},
                    "updated_at": "2026-04-11T01:00:00+00:00",
                },
            ) as set_app_setting,
            mock.patch.object(
                control_plane_app,
                "_classifier_profile_status",
                return_value={
                    "requested_profile": "backfill",
                    "active_profile": "backfill",
                    "profiles": [
                        {
                            "key": "live",
                            "configured": True,
                        },
                        {
                            "key": "backfill",
                            "configured": True,
                        },
                    ],
                    "updated_at": "2026-04-11T01:00:00+00:00",
                },
            ),
            mock.patch.object(control_plane_app, "record_audit"),
            mock.patch.object(control_plane_app, "_clear_service_snapshot_cache"),
        ):
            response = control_plane_app.set_classifier_profile(payload, auth=None)

        self.assertEqual(response["active_profile"], "backfill")
        self.assertEqual(set_app_setting.call_args.args[0], control_plane_app.CLASSIFIER_PROFILE_SETTING_KEY)
        self.assertEqual(set_app_setting.call_args.args[1]["profile"], "backfill")

    def test_classifier_profile_status_uses_modelcar_when_requested_and_configured(self) -> None:
        with (
            mock.patch.object(
                control_plane_app,
                "classifier_profile_catalog",
                return_value={
                    "live": {
                        "key": "live",
                        "label": "Live model",
                        "description": "Live path",
                        "endpoint": "http://predictive-live.example.com",
                        "model_name": "ani-predictive-fs",
                        "model_version_label": "ani-predictive-fs",
                        "configured": True,
                    },
                    "backfill": {
                        "key": "backfill",
                        "label": "Backfill model",
                        "description": "Backfill path",
                        "endpoint": "http://predictive-backfill.example.com",
                        "model_name": "ani-predictive-backfill",
                        "model_version_label": "ani-predictive-backfill",
                        "configured": True,
                    },
                    "modelcar": {
                        "key": "modelcar",
                        "label": "Modelcar model",
                        "description": "OCI path",
                        "endpoint": "http://predictive-modelcar.example.com",
                        "model_name": "ani-predictive-backfill-modelcar",
                        "model_version_label": "ani-predictive-backfill-modelcar",
                        "configured": True,
                    },
                },
            ),
            mock.patch.object(
                control_plane_app,
                "_probe_service",
                side_effect=[
                    {"ok": True, "status": "ready"},
                    {"ok": True, "status": "ready"},
                    {"ok": True, "status": "ready"},
                ],
            ),
            mock.patch.object(
                control_plane_app,
                "get_app_setting_record",
                return_value={
                    "key": control_plane_app.CLASSIFIER_PROFILE_SETTING_KEY,
                    "value": {"profile": "modelcar"},
                    "updated_at": "2026-04-16T00:00:00+00:00",
                },
            ),
        ):
            status = control_plane_app._classifier_profile_status()

        self.assertEqual(status["requested_profile"], "modelcar")
        self.assertEqual(status["active_profile"], "modelcar")
        self.assertTrue(next(item for item in status["profiles"] if item["key"] == "modelcar")["active"])


class ServiceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        control_plane_app._clear_service_snapshot_cache()

    def tearDown(self) -> None:
        control_plane_app._clear_service_snapshot_cache()

    def test_service_snapshot_uses_modelcar_and_skips_empty_backfill_endpoint(self) -> None:
        def probe(name: str, endpoint: str, path: str = "/healthz") -> dict[str, object]:
            return {
                "name": name,
                "ok": True,
                "status": "ready",
                "endpoint": endpoint,
                "payload": {"path": path},
            }

        with (
            mock.patch.object(control_plane_app, "healthz", return_value={"status": "ok"}),
            mock.patch.object(control_plane_app, "FEATURE_GATEWAY_URL", "http://feature-gateway.example.com"),
            mock.patch.object(control_plane_app, "ANOMALY_SERVICE_URL", "http://anomaly-service.example.com"),
            mock.patch.object(control_plane_app, "RCA_SERVICE_URL", "http://rca-service.example.com"),
            mock.patch.object(control_plane_app, "PREDICTIVE_SERVICE_URL", "http://predictive-live.example.com"),
            mock.patch.object(control_plane_app, "PREDICTIVE_BACKFILL_SERVICE_URL", ""),
            mock.patch.object(control_plane_app, "PREDICTIVE_MODELCAR_SERVICE_URL", "http://predictive-modelcar.example.com"),
            mock.patch.object(control_plane_app, "_probe_service", side_effect=probe),
        ):
            services = control_plane_app._service_snapshot()

        self.assertEqual(
            [service["name"] for service in services],
            [
                "Control Plane",
                "Feature Gateway",
                "Anomaly Service",
                "RCA Service",
                "Predictive Service",
                "Modelcar Predictive Service",
            ],
        )
        self.assertEqual(services[-1]["payload"]["path"], "/v2/health/ready")

    def test_service_snapshot_deduplicates_predictive_endpoints(self) -> None:
        def probe(name: str, endpoint: str, path: str = "/healthz") -> dict[str, object]:
            return {
                "name": name,
                "ok": True,
                "status": "ready",
                "endpoint": endpoint,
                "payload": {"path": path},
            }

        shared_endpoint = "http://predictive-shared.example.com"
        with (
            mock.patch.object(control_plane_app, "healthz", return_value={"status": "ok"}),
            mock.patch.object(control_plane_app, "FEATURE_GATEWAY_URL", "http://feature-gateway.example.com"),
            mock.patch.object(control_plane_app, "ANOMALY_SERVICE_URL", "http://anomaly-service.example.com"),
            mock.patch.object(control_plane_app, "RCA_SERVICE_URL", "http://rca-service.example.com"),
            mock.patch.object(control_plane_app, "PREDICTIVE_SERVICE_URL", shared_endpoint),
            mock.patch.object(control_plane_app, "PREDICTIVE_BACKFILL_SERVICE_URL", shared_endpoint),
            mock.patch.object(control_plane_app, "PREDICTIVE_MODELCAR_SERVICE_URL", shared_endpoint),
            mock.patch.object(control_plane_app, "_probe_service", side_effect=probe),
        ):
            services = control_plane_app._service_snapshot()

        predictive_services = [service for service in services if service["payload"].get("path") == "/v2/health/ready"]
        self.assertEqual(len(predictive_services), 1)
        self.assertEqual(predictive_services[0]["name"], "Predictive Service")


if __name__ == "__main__":
    unittest.main()
