#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path
from typing import Any

import yaml


class ParseError(ValueError):
    """Raised when the Lightspeed response cannot be converted into a callback payload."""


SUPPORTED_ACTION_BY_ANOMALY_TYPE: dict[str, str] = {
    "normal": "rate_limit_pcscf",
    "registration_storm": "rate_limit_pcscf",
    "registration_failure": "quarantine_imsi",
    "authentication_failure": "quarantine_imsi",
    "malformed_sip": "quarantine_imsi",
    "routing_error": "rate_limit_pcscf",
    "busy_destination": "scale_scscf",
    "call_setup_timeout": "scale_scscf",
    "call_drop_mid_session": "scale_scscf",
    "server_internal_error": "scale_scscf",
    "network_degradation": "rate_limit_pcscf",
    "retransmission_spike": "rate_limit_pcscf",
    "unknown": "rate_limit_pcscf",
}


def _extract_prompt_field(prompt: str, field_name: str) -> str:
    match = re.search(rf"(?m)^- {re.escape(field_name)}:\s*(.+?)\s*$", prompt or "")
    return match.group(1).strip() if match else ""


def _extract_yaml_body(raw_response: str) -> str:
    text = str(raw_response or "").strip()
    fenced = re.search(r"```(?:yaml)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    return (fenced.group(1) if fenced else text).strip()


def _normalize_preconditions(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        items: list[str] = []
        for entry in value:
            cleaned = str(entry or "").strip()
            if cleaned:
                items.append(cleaned)
        return items
    cleaned = str(value).strip()
    return [cleaned] if cleaned else []


def _ensure_yaml_document(playbook_text: str) -> str:
    normalized = str(playbook_text or "").strip()
    if not normalized:
        raise ParseError("Lightspeed did not return playbook YAML.")
    if not normalized.startswith("---"):
        normalized = f"---\n{normalized}"
    if not normalized.endswith("\n"):
        normalized = f"{normalized}\n"
    return normalized


def _quote_problematic_template_scalars(playbook_yaml: str) -> str:
    repaired_lines: list[str] = []
    changed = False

    for line in str(playbook_yaml or "").splitlines():
        match = re.match(r"^(\s*(?:-\s+)?[^:#]+:\s+)(.+)$", line)
        if not match:
            repaired_lines.append(line)
            continue

        prefix, value = match.groups()
        stripped = value.strip()
        if (
            stripped
            and "{{" in stripped
            and (stripped.startswith("{{") or stripped[0] not in "\"'|>[{!&*")
            and (
                ": " in stripped
                or stripped.startswith("{{")
                or re.fullmatch(r"\{\{.*\}\}", stripped) is not None
            )
        ):
            escaped = stripped.replace("\\", "\\\\").replace('"', '\\"')
            repaired_lines.append(f'{prefix}"{escaped}"')
            changed = True
            continue

        repaired_lines.append(line)

    if not changed:
        return playbook_yaml
    return "\n".join(repaired_lines) + ("\n" if str(playbook_yaml or "").endswith("\n") else "")


def _reindent_root_level_play_sections(playbook_yaml: str) -> str:
    play_section_keys = {
        "any_errors_fatal",
        "become",
        "become_user",
        "collections",
        "environment",
        "force_handlers",
        "gather_facts",
        "handlers",
        "hosts",
        "max_fail_percentage",
        "module_defaults",
        "name",
        "post_tasks",
        "pre_tasks",
        "roles",
        "serial",
        "strategy",
        "tasks",
        "vars",
        "vars_files",
    }
    repaired_lines: list[str] = []
    changed = False
    seen_play = False
    inside_reindented_block = False

    for line in str(playbook_yaml or "").splitlines():
        stripped = line.strip()
        if not seen_play:
            if re.match(r"^-\s+(?:name|hosts)\s*:", line):
                seen_play = True
            repaired_lines.append(line)
            continue

        if re.match(r"^-\s+(?:name|hosts)\s*:", line):
            inside_reindented_block = False
            repaired_lines.append(line)
            continue

        root_key_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s+.*)?$", line)
        if root_key_match and root_key_match.group(1) in play_section_keys:
            repaired_lines.append(f"  {line}")
            inside_reindented_block = True
            changed = True
            continue

        if inside_reindented_block:
            if not stripped:
                repaired_lines.append(line)
                continue
            if line.startswith(" ") or line.startswith("\t"):
                repaired_lines.append(f"  {line}")
                changed = True
                continue
            inside_reindented_block = False

        repaired_lines.append(line)

    if not changed:
        return playbook_yaml
    return "\n".join(repaired_lines) + ("\n" if str(playbook_yaml or "").endswith("\n") else "")


def _load_metadata_mapping(metadata_text: str) -> dict[str, Any]:
    try:
        parsed_documents = [document for document in yaml.safe_load_all(metadata_text) if document is not None]
    except yaml.YAMLError as exc:
        raise ParseError(f"Failed to parse the Lightspeed metadata block: {exc}") from exc
    if not parsed_documents:
        return {}
    if len(parsed_documents) != 1 or not isinstance(parsed_documents[0], dict):
        raise ParseError("The Lightspeed metadata block was not a single YAML mapping.")
    return parsed_documents[0]


def _trim_playbook_body(playbook_text: str) -> str:
    lines = str(playbook_text or "").strip().splitlines()
    if not lines:
        return ""

    trimmed: list[str] = []
    seen_play = False
    for line in lines:
        stripped = line.strip()
        if not seen_play:
            if not stripped:
                continue
            if stripped == "---":
                trimmed.append(line)
                continue
            if re.match(r"^-\s+(?:name|hosts)\s*:", line):
                seen_play = True
                trimmed.append(line)
                continue
            continue

        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:\s*(?:.+)?$", line):
            break
        trimmed.append(line)

    return "\n".join(trimmed).strip()


def _extract_metadata_and_playbook(yaml_body: str) -> tuple[dict[str, Any], str]:
    cleaned = str(yaml_body or "").strip()
    if not cleaned:
        raise ParseError("Lightspeed returned an empty response.")

    if cleaned.startswith("---"):
        cleaned = cleaned[3:].lstrip("\r\n")

    try:
        parsed_document = yaml.safe_load(cleaned)
    except yaml.YAMLError:
        parsed_document = None

    if isinstance(parsed_document, dict) and "playbook_yaml" in parsed_document:
        metadata = dict(parsed_document)
        playbook_yaml = _ensure_yaml_document(metadata.pop("playbook_yaml", ""))
        playbook_yaml = _validate_playbook_yaml(playbook_yaml)
        return metadata, playbook_yaml

    envelope_match = re.search(r"(?m)^playbook_yaml:\s*(?:[>|][-+]?)?\s*$", cleaned)
    if envelope_match:
        metadata_text = cleaned[: envelope_match.start()].strip()
        playbook_text = textwrap.dedent(cleaned[envelope_match.end() :])
        playbook_text = _trim_playbook_body(playbook_text)
        metadata = _load_metadata_mapping(metadata_text) if metadata_text else {}
        playbook_yaml = _ensure_yaml_document(playbook_text)
        playbook_yaml = _validate_playbook_yaml(playbook_yaml)
        return metadata, playbook_yaml

    lines = cleaned.splitlines()
    playbook_start = None
    for index, line in enumerate(lines):
        if re.match(r"^-\s+(?:name|hosts)\s*:", line):
            playbook_start = index
            break

    if playbook_start is None:
        raise ParseError("No top-level Ansible play entry was found in the Lightspeed response.")

    metadata_text = "\n".join(lines[:playbook_start]).strip()
    playbook_text = "\n".join(lines[playbook_start:]).strip()

    metadata: dict[str, Any] = {}
    if metadata_text:
        metadata = _load_metadata_mapping(metadata_text)

    playbook_yaml = _ensure_yaml_document(_trim_playbook_body(playbook_text))
    playbook_yaml = _validate_playbook_yaml(playbook_yaml)
    return metadata, playbook_yaml


def _validate_playbook_yaml(playbook_yaml: str) -> str:
    try:
        parsed = yaml.safe_load(playbook_yaml)
    except yaml.YAMLError as exc:
        repaired = playbook_yaml
        for repair in (_quote_problematic_template_scalars, _reindent_root_level_play_sections):
            repaired = repair(repaired)
        if repaired != playbook_yaml:
            try:
                parsed = yaml.safe_load(repaired)
            except yaml.YAMLError:
                raise ParseError(f"Failed to parse generated playbook YAML: {exc}") from exc
            playbook_yaml = repaired
        else:
            raise ParseError(f"Failed to parse generated playbook YAML: {exc}") from exc
    if not isinstance(parsed, list):
        raise ParseError("Generated playbook YAML must be a YAML list of plays.")
    return playbook_yaml


def _supported_playbook_catalog() -> dict[str, dict[str, Any]]:
    playbook_dir = Path(__file__).resolve().parent / "playbooks"
    return {
        "rate_limit_pcscf": {
            "action_ref": "rate_limit_pcscf",
            "playbook_ref": "rate_limit_pcscf",
            "title": "Rate limit the P-CSCF ingress path",
            "summary": "Apply the supported ingress guardrail for the P-CSCF path.",
            "description": "Apply the supported ingress guardrail for the P-CSCF path.",
            "expected_outcome": "Retry storms slow down and downstream control-plane components recover.",
            "preconditions": ["Operator approval", "Ingress rate limit policy available"],
            "playbook_path": playbook_dir / "rate-limit-pcscf.yaml",
        },
        "scale_scscf": {
            "action_ref": "scale_scscf",
            "playbook_ref": "scale_scscf",
            "title": "Scale the S-CSCF path",
            "summary": "Use the supported S-CSCF scaling playbook.",
            "description": "Use the supported S-CSCF scaling playbook.",
            "expected_outcome": "Registration or session setup latency stabilizes and retry volume decreases.",
            "preconditions": ["Operator approval", "Scaling guardrails available"],
            "playbook_path": playbook_dir / "scale-scscf.yaml",
        },
        "quarantine_imsi": {
            "action_ref": "quarantine_imsi",
            "playbook_ref": "quarantine_imsi",
            "title": "Quarantine the offending subscriber or traffic source",
            "summary": "Use the supported quarantine playbook for the offending subscriber or traffic source.",
            "description": "Use the supported quarantine playbook for the offending subscriber or traffic source.",
            "expected_outcome": "Malformed or abusive traffic stops while the rest of the network remains stable.",
            "preconditions": ["Operator approval", "Source identity confirmed"],
            "playbook_path": playbook_dir / "quarantine-imsi.yaml",
        },
    }


def _render_supported_playbook(action_ref: str) -> str:
    catalog = _supported_playbook_catalog().get(action_ref) or {}
    playbook_path = catalog.get("playbook_path")
    if not isinstance(playbook_path, Path) or not playbook_path.exists():
        return ""
    content = playbook_path.read_text(encoding="utf-8").strip()
    if not content:
        return ""
    if not content.endswith("\n"):
        content = f"{content}\n"
    return content


def _supported_action_from_text(text: str) -> str:
    haystack = str(text or "").strip().lower()
    if not haystack:
        return ""
    if any(token in haystack for token in ("quarantine", "imsi", "subscriber isolation", "offending subscriber")):
        return "quarantine_imsi"
    if (
        (("p-cscf" in haystack) or ("pcscf" in haystack))
        and any(token in haystack for token in ("rate limit", "guardrail", "retry", "retransmission", "ingress"))
    ):
        return "rate_limit_pcscf"
    if (
        (("s-cscf" in haystack) or ("scscf" in haystack))
        and any(token in haystack for token in ("scale", "replica", "capacity", "latency", "timeout", "busy"))
    ):
        return "scale_scscf"
    if "rate limit" in haystack:
        return "rate_limit_pcscf"
    if "scale the s-cscf" in haystack or "scale scscf" in haystack:
        return "scale_scscf"
    return ""


def _supported_action_for_prompt(prompt: str, raw_response: str) -> str:
    response_hint = _supported_action_from_text(raw_response)
    if response_hint:
        return response_hint

    anomaly_type = _extract_prompt_field(prompt, "anomaly_type").strip().lower()
    if anomaly_type:
        return SUPPORTED_ACTION_BY_ANOMALY_TYPE.get(anomaly_type, SUPPORTED_ACTION_BY_ANOMALY_TYPE["unknown"])

    prompt_hint = _supported_action_from_text(prompt)
    if prompt_hint:
        return prompt_hint
    return ""


def _build_supported_callback_payload(
    *,
    action_ref: str,
    callback_url: str,
    correlation_id: str,
    provider_name: str,
    provider_run_id: str,
    error_text: str = "",
    preserve_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog = _supported_playbook_catalog().get(action_ref) or {}
    playbook_yaml = _render_supported_playbook(action_ref)
    if not playbook_yaml:
        raise ParseError(f"Supported fallback playbook '{action_ref}' was not available.")

    preserve = dict(preserve_fields or {})
    title = str(preserve.get("title") or catalog.get("title") or "").strip()
    summary = str(preserve.get("summary") or preserve.get("description") or catalog.get("summary") or "").strip()
    description = str(preserve.get("description") or summary or catalog.get("description") or title).strip()
    expected_outcome = str(
        preserve.get("expected_outcome") or catalog.get("expected_outcome") or ""
    ).strip()
    preconditions = _normalize_preconditions(
        preserve.get("preconditions") if "preconditions" in preserve else catalog.get("preconditions")
    )
    metadata = preserve.get("metadata") if isinstance(preserve.get("metadata"), dict) else {}

    return {
        "callback_url": callback_url,
        "correlation_id": correlation_id,
        "status": "generated",
        "title": title,
        "description": description,
        "summary": summary,
        "expected_outcome": expected_outcome,
        "preconditions": preconditions,
        "playbook_yaml": playbook_yaml,
        "playbook_ref": str(preserve.get("playbook_ref") or catalog.get("playbook_ref") or action_ref).strip(),
        "action_ref": str(preserve.get("action_ref") or catalog.get("action_ref") or action_ref).strip(),
        "provider_name": str(preserve.get("provider_name") or provider_name).strip() or provider_name,
        "provider_run_id": str(preserve.get("provider_run_id") or provider_run_id).strip(),
        "error": "",
        "metadata": metadata
        | {
            "supported_action_ref": action_ref,
            "environment_normalized": True,
            "environment_normalization_reason": (
                f"Replaced the model-generated playbook body with the supported '{action_ref}' repo template."
            ),
        }
        | (
            {
                "supported_fallback_template": True,
                "generation_fallback_reason": "supported_template_from_parse_failure",
                "generation_fallback_error": error_text,
            }
            if error_text
            else {}
        ),
    }


def build_callback_payload(
    *,
    prompt: str,
    raw_response: str,
    provider_run_id: str = "",
    provider_name: str = "Ansible Lightspeed",
) -> dict[str, Any]:
    callback_url = _extract_prompt_field(prompt, "callback_url")
    correlation_id = _extract_prompt_field(prompt, "correlation_id")

    payload: dict[str, Any] = {
        "callback_url": callback_url,
        "correlation_id": correlation_id,
        "status": "generated",
        "title": "",
        "description": "",
        "summary": "",
        "expected_outcome": "",
        "preconditions": [],
        "playbook_yaml": "",
        "playbook_ref": "",
        "action_ref": "",
        "provider_name": provider_name,
        "provider_run_id": str(provider_run_id or "").strip(),
        "error": "",
        "metadata": {},
    }

    if not callback_url:
        payload["status"] = "failed"
        payload["error"] = "Callback URL was not present in the Lightspeed prompt."
        return payload
    if not correlation_id:
        payload["status"] = "failed"
        payload["error"] = "Correlation ID was not present in the Lightspeed prompt."
        return payload

    supported_action_ref = _supported_action_for_prompt(prompt, raw_response)

    try:
        metadata, playbook_yaml = _extract_metadata_and_playbook(_extract_yaml_body(raw_response))
    except ParseError as exc:
        if supported_action_ref:
            return _build_supported_callback_payload(
                action_ref=supported_action_ref,
                callback_url=callback_url,
                correlation_id=correlation_id,
                provider_name=provider_name,
                provider_run_id=provider_run_id,
                error_text=str(exc),
            )
        payload["status"] = "failed"
        payload["error"] = str(exc)
        return payload

    title = str(metadata.get("title") or "").strip()
    summary = str(metadata.get("summary") or metadata.get("description") or "").strip()
    description = str(metadata.get("description") or summary or title).strip()
    expected_outcome = str(metadata.get("expected_outcome") or "").strip()
    known_metadata_fields = {
        "title",
        "description",
        "summary",
        "expected_outcome",
        "preconditions",
        "playbook_ref",
        "action_ref",
        "provider_name",
        "provider_run_id",
        "status",
        "callback_url",
        "correlation_id",
        "playbook_yaml",
    }

    if supported_action_ref:
        return _build_supported_callback_payload(
            action_ref=supported_action_ref,
            callback_url=callback_url,
            correlation_id=correlation_id,
            provider_name=provider_name,
            provider_run_id=provider_run_id,
            preserve_fields={
                "title": title,
                "description": description,
                "summary": summary,
                "expected_outcome": expected_outcome,
                "preconditions": metadata.get("preconditions"),
                "playbook_ref": metadata.get("playbook_ref"),
                "action_ref": metadata.get("action_ref"),
                "provider_name": metadata.get("provider_name"),
                "provider_run_id": metadata.get("provider_run_id"),
                "metadata": {key: value for key, value in metadata.items() if key not in known_metadata_fields},
            },
        )

    payload.update(
        {
            "status": str(metadata.get("status") or "generated").strip() or "generated",
            "title": title,
            "description": description,
            "summary": summary,
            "expected_outcome": expected_outcome,
            "preconditions": _normalize_preconditions(metadata.get("preconditions")),
            "playbook_yaml": playbook_yaml,
            "playbook_ref": str(metadata.get("playbook_ref") or "").strip(),
            "action_ref": str(metadata.get("action_ref") or "").strip(),
            "provider_name": str(metadata.get("provider_name") or provider_name).strip() or provider_name,
            "provider_run_id": str(metadata.get("provider_run_id") or provider_run_id).strip(),
            "metadata": {key: value for key, value in metadata.items() if key not in known_metadata_fields},
        }
    )
    return payload


def main() -> int:
    payload = build_callback_payload(
        prompt=os.getenv("LIGHTSPEED_PROMPT", ""),
        raw_response=os.getenv("LIGHTSPEED_RESPONSE", ""),
        provider_run_id=os.getenv("LIGHTSPEED_PROVIDER_RUN_ID", ""),
        provider_name=os.getenv("LIGHTSPEED_PROVIDER_NAME", "Ansible Lightspeed"),
    )
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
