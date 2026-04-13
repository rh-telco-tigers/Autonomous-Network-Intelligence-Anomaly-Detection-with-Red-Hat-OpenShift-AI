from __future__ import annotations

from textwrap import dedent

from automation.ansible.parse_lightspeed_response import build_callback_payload


def test_build_callback_payload_splits_metadata_from_fenced_playbook_response() -> None:
    prompt = dedent(
        """
        Generation requirements:
        - return one safe, idempotent Ansible playbook in YAML

        Callback contract:
        - callback_url: http://control-plane.ani-runtime.svc.cluster.local:8080/incidents/inc-1/playbook-generation/callback
        - correlation_id: corr-123
        """
    ).strip()
    raw_response = dedent(
        """
        ```yaml
        ---
        title: "IMS Registration Storm Retry Amplification Mitigation"
        summary: "Mitigate retry amplification on the registration path."
        preconditions:
          - "Review the current incident context."
        expected_outcome: "Registration pressure is reduced."

        ---
        - name: "Mitigate IMS Registration Storm"
          hosts: localhost
          gather_facts: false
          tasks:
            - name: "Scale registration workers"
              debug:
                msg: "safe change"
        ```

        Additional explanatory prose that should be ignored.
        """
    ).strip()

    payload = build_callback_payload(
        prompt=prompt,
        raw_response=raw_response,
        provider_run_id="conv-1",
    )

    assert payload["status"] == "generated"
    assert payload["callback_url"].endswith("/incidents/inc-1/playbook-generation/callback")
    assert payload["correlation_id"] == "corr-123"
    assert payload["title"] == "IMS Registration Storm Retry Amplification Mitigation"
    assert payload["summary"] == "Mitigate retry amplification on the registration path."
    assert payload["preconditions"] == ["Review the current incident context."]
    assert payload["expected_outcome"] == "Registration pressure is reduced."
    assert payload["provider_run_id"] == "conv-1"
    assert payload["playbook_yaml"].startswith("---\n- name: \"Mitigate IMS Registration Storm\"")
    assert payload["error"] == ""


def test_build_callback_payload_accepts_envelope_with_playbook_yaml() -> None:
    prompt = dedent(
        """
        Callback contract:
        - callback_url: http://control-plane.ani-runtime.svc.cluster.local:8080/incidents/inc-2/playbook-generation/callback
        - correlation_id: corr-456
        """
    ).strip()
    raw_response = dedent(
        """
        ```yaml
        title: "Apply safe rollback"
        description: "Rollback the canary deployment."
        expected_outcome: "Canary is disabled."
        preconditions: "Confirm the canary is the source of the incident."
        playbook_yaml: |
          ---
          - hosts: localhost
            gather_facts: false
            tasks:
              - debug:
                  msg: rollback
        ```
        """
    ).strip()

    payload = build_callback_payload(prompt=prompt, raw_response=raw_response)

    assert payload["status"] == "generated"
    assert payload["title"] == "Apply safe rollback"
    assert payload["description"] == "Rollback the canary deployment."
    assert payload["preconditions"] == ["Confirm the canary is the source of the incident."]
    assert payload["playbook_yaml"].startswith("---\n- hosts: localhost")
    assert payload["error"] == ""


def test_build_callback_payload_handles_malformed_envelope_playbook_yaml_section() -> None:
    prompt = dedent(
        """
        Callback contract:
        - callback_url: http://control-plane.ani-runtime.svc.cluster.local:8080/incidents/inc-4/playbook-generation/callback
        - correlation_id: corr-999
        """
    ).strip()
    raw_response = dedent(
        """
        ```yaml
        ---
        title: "Apply retry guardrail"
        summary: "Reduce registration retry pressure."
        playbook_ref: "ani-demo-registration-storm-remediation"
        action_ref: "IMS-registration-storm-retry-amplification"
        provider_name: "Ansible Code Generator"
        provider_run_id: "corr-999"
        status: "generated"
        callback_url: "http://control-plane.ani-runtime.svc.cluster.local:8080/incidents/inc-4/playbook-generation/callback"
        correlation_id: "corr-999"
        playbook_yaml:
        ---
        - name: "Apply retry guardrail"
          hosts: localhost
          gather_facts: false
          tasks:
            - debug:
                msg: guardrail

        metadata:
          creation_timestamp: "2022-03-15T12:00:00Z"
        ```
        """
    ).strip()

    payload = build_callback_payload(prompt=prompt, raw_response=raw_response)

    assert payload["status"] == "generated"
    assert payload["title"] == "Apply retry guardrail"
    assert payload["summary"] == "Reduce registration retry pressure."
    assert payload["playbook_ref"] == "ani-demo-registration-storm-remediation"
    assert payload["action_ref"] == "IMS-registration-storm-retry-amplification"
    assert payload["provider_name"] == "Ansible Code Generator"
    assert payload["provider_run_id"] == "corr-999"
    assert payload["playbook_yaml"] == (
        "---\n"
        "- name: \"Apply retry guardrail\"\n"
        "  hosts: localhost\n"
        "  gather_facts: false\n"
        "  tasks:\n"
        "    - debug:\n"
        "        msg: guardrail\n"
    )
    assert payload["error"] == ""


def test_build_callback_payload_reports_parse_failure_without_losing_correlation() -> None:
    prompt = dedent(
        """
        Callback contract:
        - callback_url: http://control-plane.ani-runtime.svc.cluster.local:8080/incidents/inc-3/playbook-generation/callback
        - correlation_id: corr-789
        """
    ).strip()

    payload = build_callback_payload(prompt=prompt, raw_response="No YAML was returned.")

    assert payload["status"] == "failed"
    assert payload["callback_url"].endswith("/incidents/inc-3/playbook-generation/callback")
    assert payload["correlation_id"] == "corr-789"
    assert payload["playbook_yaml"] == ""
    assert "No top-level Ansible play entry" in payload["error"]


def test_build_callback_payload_repairs_unquoted_template_scalar_with_colon() -> None:
    prompt = dedent(
        """
        Callback contract:
        - callback_url: http://control-plane.ani-runtime.svc.cluster.local:8080/incidents/inc-5/playbook-generation/callback
        - correlation_id: corr-1000
        """
    ).strip()
    raw_response = dedent(
        """
        ```yaml
        title: "Scale S-CSCF safely"
        summary: "Raise capacity with verification."
        playbook_yaml: |
          ---
          - name: Verify scaling result
            hosts: localhost
            gather_facts: false
            tasks:
              - name: Fail when scaling did not settle
                ansible.builtin.fail:
                  msg: Scaling failed. Current replicas: {{ check_result.json.status.replicas | default('unknown') }}
        ```
        """
    ).strip()

    payload = build_callback_payload(prompt=prompt, raw_response=raw_response)

    assert payload["status"] == "generated"
    assert payload["error"] == ""
    assert (
        'msg: "Scaling failed. Current replicas: {{ check_result.json.status.replicas | default(\'unknown\') }}"'
        in payload["playbook_yaml"]
    )
