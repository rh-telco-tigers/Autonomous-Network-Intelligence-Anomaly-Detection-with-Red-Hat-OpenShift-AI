from __future__ import annotations

from pathlib import Path

import yaml


def test_generate_playbook_rulebook_uses_stable_kafka_consumer_group() -> None:
    rulebook_path = Path(__file__).resolve().parents[2] / "rulebooks" / "generate-playbook-event.yml"
    documents = yaml.safe_load(rulebook_path.read_text(encoding="utf-8"))

    assert isinstance(documents, list) and documents
    sources = documents[0]["sources"]
    kafka_source = sources[0]["ansible.eda.kafka"]

    assert kafka_source["topic"] == "aiops-ansible-playbook-generate-instruction"
    assert kafka_source["group_id"] == "ani-remediation-playbook-generation"
    assert kafka_source["offset"] == "latest"


def test_lightspeed_prompt_avoids_literal_jinja_examples() -> None:
    playbook_path = Path(__file__).resolve().parents[2] / "automation" / "ansible" / "playbooks" / "ani-remediation.yaml"
    contents = playbook_path.read_text(encoding="utf-8")

    assert "Jinja like {{ ... }}" not in contents
    assert contents.count("double curly braces") >= 2
