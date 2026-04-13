#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from typing import Any

import yaml


class ParseError(ValueError):
    """Raised when the Lightspeed response cannot be converted into a callback payload."""


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
        _validate_playbook_yaml(playbook_yaml)
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

    playbook_yaml = _ensure_yaml_document(playbook_text)
    _validate_playbook_yaml(playbook_yaml)
    return metadata, playbook_yaml


def _validate_playbook_yaml(playbook_yaml: str) -> None:
    try:
        parsed = yaml.safe_load(playbook_yaml)
    except yaml.YAMLError as exc:
        raise ParseError(f"Failed to parse generated playbook YAML: {exc}") from exc
    if not isinstance(parsed, list):
        raise ParseError("Generated playbook YAML must be a YAML list of plays.")


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

    try:
        metadata, playbook_yaml = _extract_metadata_and_playbook(_extract_yaml_body(raw_response))
    except ParseError as exc:
        payload["status"] = "failed"
        payload["error"] = str(exc)
        return payload

    title = str(metadata.get("title") or "").strip()
    summary = str(metadata.get("summary") or metadata.get("description") or "").strip()
    description = str(metadata.get("description") or summary or title).strip()
    expected_outcome = str(metadata.get("expected_outcome") or "").strip()

    payload.update(
        {
            "title": title,
            "description": description,
            "summary": summary,
            "expected_outcome": expected_outcome,
            "preconditions": _normalize_preconditions(metadata.get("preconditions")),
            "playbook_yaml": playbook_yaml,
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
