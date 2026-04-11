# Manual GPU MachineSet

This directory is for manual Machine API assets only. Do not add it to any GitOps or Argo CD path.

The GPU worker pool only adds AWS capacity. The cluster still needs the GPU operator stack to expose `nvidia.com/gpu` on the node. In the source cluster that also included a `ClusterPolicy/gpu-cluster-policy`.

## What the repo includes

- `aws-gpu-machineset-template.yaml`: a reference template showing the fields that matter for an AWS GPU worker pool
- `render_gpu_machineset.py`: a helper that clones an existing worker `MachineSet` from the currently logged-in cluster and swaps in the GPU-specific values

## Recommended usage

Render a cluster-specific manifest without applying it:

```sh
make render-gpu-node-pool
```

Render and apply a single-replica GPU worker pool to the current cluster:

```sh
make add-gpu-node-pool
```

Verify that the new node eventually reports allocatable GPU capacity:

```sh
oc get machineset -n openshift-machine-api | rg 'gpu'
oc get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\t"}}{{index .status.allocatable "nvidia.com/gpu"}}{{"\n"}}{{end}}'
```

## Optional overrides

The defaults are chosen to work without extra parameters on the current cluster context.

- `GPU_SOURCE_MACHINESET`: clone this worker `MachineSet` instead of auto-detecting one
- `GPU_INSTANCE_TYPE`: AWS instance type to use, default `g6.8xlarge`
- `GPU_REPLICAS`: replica count for the new `MachineSet`, default `1`
- `GPU_OUTPUT`: where to write the rendered manifest; default `k8s/manual/machine-api/generated/<machineset-name>.yaml`

Example:

```sh
make add-gpu-node-pool GPU_SOURCE_MACHINESET=ocp-t82nk-worker-us-east-2b-compute GPU_REPLICAS=1
```
