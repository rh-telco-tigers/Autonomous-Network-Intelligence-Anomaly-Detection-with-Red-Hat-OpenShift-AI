#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

NAMESPACE = "openshift-machine-api"
DEFAULT_INSTANCE_TYPE = "g6.8xlarge"
DEFAULT_REPLICAS = 1
DEFAULT_GPU_COUNT = "1"
DEFAULT_VCPU = "32"
DEFAULT_MEMORY_MB = "131072"
DEFAULT_NODE_LABEL = "node-role.kubernetes.io/gpu"
DEFAULT_OUTPUT_DIR = Path("k8s/manual/machine-api/generated")


def run_oc(args: list[str], stdin: str | None = None) -> str:
    result = subprocess.run(
        ["oc", *args],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"oc {' '.join(args)} failed"
        raise SystemExit(message)
    return result.stdout


def has_gpu_cluster_policy() -> bool:
    result = subprocess.run(
        ["oc", "get", "clusterpolicy", "-o", "name"],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def oc_json(args: list[str]) -> Any:
    return json.loads(run_oc([*args, "-o", "json"]))


def detect_source_machineset() -> str:
    payload = oc_json(["get", "machineset", "-n", NAMESPACE])
    candidates: list[tuple[int, str]] = []
    for item in payload.get("items", []):
        labels = item.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
        if labels.get("machine.openshift.io/cluster-api-machine-role") != "worker":
            continue
        name = item["metadata"]["name"]
        replicas = int(item.get("spec", {}).get("replicas", 0) or 0)
        ready = int(item.get("status", {}).get("readyReplicas", 0) or 0)
        score = 0
        if name.endswith("-compute"):
            score += 100
        if replicas > 0:
            score += 20
        if ready > 0:
            score += 10
        candidates.append((score, name))
    if not candidates:
        raise SystemExit(f"No worker MachineSet found in namespace {NAMESPACE}.")
    candidates.sort(key=lambda entry: (-entry[0], entry[1]))
    return candidates[0][1]


def derive_machineset_name(source_name: str, suffix: str) -> str:
    if source_name.endswith("-compute"):
        return f"{source_name[:-len('-compute')]}-{suffix}"
    if source_name.endswith(f"-{suffix}"):
        return source_name
    return f"{source_name}-{suffix}"


def strip_runtime_fields(manifest: dict[str, Any]) -> None:
    manifest.pop("status", None)
    metadata = manifest.setdefault("metadata", {})
    for key in ("creationTimestamp", "generation", "resourceVersion", "uid", "managedFields", "selfLink"):
        metadata.pop(key, None)


def build_manifest(source: dict[str, Any], args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    manifest = copy.deepcopy(source)
    strip_runtime_fields(manifest)

    source_name = source["metadata"]["name"]
    target_name = args.name or derive_machineset_name(source_name, args.name_suffix)

    metadata = manifest.setdefault("metadata", {})
    metadata["name"] = target_name
    metadata["namespace"] = NAMESPACE
    annotations = metadata.setdefault("annotations", {})
    annotations["capacity.cluster-autoscaler.kubernetes.io/labels"] = f"kubernetes.io/arch=amd64,{args.node_label}="
    annotations["machine.openshift.io/GPU"] = args.gpu_count
    annotations["machine.openshift.io/vCPU"] = args.vcpu
    annotations["machine.openshift.io/memoryMb"] = args.memory_mb

    spec = manifest.setdefault("spec", {})
    spec["replicas"] = args.replicas

    selector_labels = spec.setdefault("selector", {}).setdefault("matchLabels", {})
    selector_labels["machine.openshift.io/cluster-api-machineset"] = target_name

    template = spec.setdefault("template", {})
    template_labels = template.setdefault("metadata", {}).setdefault("labels", {})
    template_labels["machine.openshift.io/cluster-api-machineset"] = target_name

    template_spec = template.setdefault("spec", {})
    node_metadata = template_spec.setdefault("metadata", {})
    node_labels = node_metadata.setdefault("labels", {})
    node_labels[args.node_label] = ""

    provider = template_spec.setdefault("providerSpec", {}).setdefault("value", {})
    provider["instanceType"] = args.instance_type

    return manifest, target_name


def render_yaml(manifest: dict[str, Any]) -> str:
    json_text = json.dumps(manifest)
    return run_oc(["create", "--dry-run=client", "-f", "-", "-o", "yaml"], stdin=json_text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a manual GPU MachineSet by cloning the current cluster's worker MachineSet.",
    )
    parser.add_argument("--source-machineset", help="Existing worker MachineSet to clone. Auto-detected when omitted.")
    parser.add_argument("--name", help="Explicit name for the new MachineSet.")
    parser.add_argument("--name-suffix", default="gpu", help="Suffix used when deriving the new MachineSet name.")
    parser.add_argument("--instance-type", default=DEFAULT_INSTANCE_TYPE, help="AWS instance type for the GPU node pool.")
    parser.add_argument("--replicas", type=int, default=DEFAULT_REPLICAS, help="Desired replica count for the new MachineSet.")
    parser.add_argument("--gpu-count", default=DEFAULT_GPU_COUNT, help="MachineSet GPU count annotation.")
    parser.add_argument("--vcpu", default=DEFAULT_VCPU, help="MachineSet vCPU annotation.")
    parser.add_argument("--memory-mb", default=DEFAULT_MEMORY_MB, help="MachineSet memoryMb annotation.")
    parser.add_argument("--node-label", default=DEFAULT_NODE_LABEL, help="Node label injected through Machine metadata.")
    parser.add_argument("--output", help="Where to write the rendered YAML. Defaults to k8s/manual/machine-api/generated/<name>.yaml")
    parser.add_argument("--apply", action="store_true", help="Apply the rendered MachineSet with oc apply -f.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_name = args.source_machineset or detect_source_machineset()
    source = oc_json(["get", "machineset", source_name, "-n", NAMESPACE])
    manifest, target_name = build_manifest(source, args)
    yaml_text = render_yaml(manifest)

    output_path = Path(args.output) if args.output else DEFAULT_OUTPUT_DIR / f"{target_name}.yaml"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml_text)
    print(f"Rendered {target_name} to {output_path}")

    if not has_gpu_cluster_policy():
        print(
            "Warning: no GPU Operator ClusterPolicy was found in the current cluster. "
            "The MachineSet can create the node, but Kubernetes will not expose "
            "nvidia.com/gpu until a ClusterPolicy exists.",
            file=sys.stderr,
        )

    if args.apply:
        apply_output = run_oc(["apply", "-f", str(output_path)])
        sys.stdout.write(apply_output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
