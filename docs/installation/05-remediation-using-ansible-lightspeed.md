# 05 — Remediation Using Ansible Lightspeed

This guide walks through enabling the AI-driven remediation workflow, which uses Ansible Lightspeed to generate and execute remediation playbooks in response to IMS anomaly events detected by the EDA Kafka pipeline.

---

## Prerequisites

- The platform has been deployed via the bootstrap (see [02-installation.md](02-installation.md))
- The `ani-aap` ArgoCD application is `Synced` and `Healthy`
- The control-plane automation bootstrap has completed successfully after the AAP license import
- You have `oc` CLI access with cluster-admin permissions

---

## Step 1 — Configure the Lightspeed Secret in Gitea

The `lightspeed-secret` is already deployed in the cluster by ArgoCD. To update it, edit the file **directly in Gitea** — do not use `oc edit` or `oc create`, as ArgoCD self-heal will immediately revert any in-cluster change back to whatever is stored in Gitea.

1. Get the Gitea URL:
   ```bash
   oc get route gitea -n gitea -o jsonpath='{.spec.host}'
   ```

2. Open the Gitea UI in your browser and navigate to:
   ```
   IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI
     → k8s → base → aap-platform → ansible-lightspeed-secret.yaml
   ```

3. Click the **pencil (edit) icon** and replace the placeholder values with your LLM backend details:

   ```yaml
   stringData:
     chatbot_model: granite-3-2-8b-instruct  # update if using a different model
     chatbot_url: <url>                       # replace with your LLM endpoint URL
     chatbot_token: <token>                   # replace with your LLM API token
   ```

4. Scroll down, add a commit message (e.g. `Configure Lightspeed LLM backend`), and click **Commit Changes**.

ArgoCD will detect the commit and apply the updated secret to the cluster within seconds. Confirm it has been applied:

```bash
oc get secret lightspeed-secret -n aap -o jsonpath='{.data.chatbot_url}' | base64 -d && echo
```

---

## Step 2 — Roll Out the Ansible Lightspeed Pods

Restart the Lightspeed deployments to pick up the updated secret:

```bash
oc rollout restart deployment/ansible-lightspeed -n aap
oc rollout restart deployment/ansible-lightspeed-api -n aap
```

---

## Step 3 — Verify the Pods are Running

Wait for both deployments to complete their rollout:

```bash
oc rollout status deployment/ansible-lightspeed -n aap
oc rollout status deployment/ansible-lightspeed-api -n aap
```

Confirm all pods are in `Running` state:

```bash
oc get pods -n aap | grep lightspeed
```

Both deployments should show `1/1 Running` before continuing.

---

## Step 4 — Create an Ansible Lightspeed API Token

### 4.1 — Find the Lightspeed Route

Retrieve the Ansible Lightspeed route from the `aap` namespace:

```bash
oc get route -n aap | grep lightspeed
```

Note the hostname — referred to as `<lightspeed_route>` in the steps below.

### 4.2 — Access the Admin Portal

Open the Django administration portal in your browser:

```
https://<lightspeed_route>/admin
```

### 4.3 — Log In as Administrator

Use the following credentials:

| Field | Value |
|-------|-------|
| **Username** | `admin` |
| **Password** | The value of the secret named `<lightspeed-custom-resource-name>-admin-password` in the `aap` namespace |

Retrieve the password with:

```bash
oc get secret -n aap | grep lightspeed | grep admin-password
oc get secret <lightspeed-custom-resource-name>-admin-password -n aap \
  -o jsonpath='{.data.password}' | base64 -d && echo
```

### 4.4 — Verify the Platform User

1. On the **Django administration** window, select **Users** from the **Users** area.
2. Confirm that the `admin` user is listed.

### 4.5 — Create an Access Token

1. From the **Django OAuth toolkit** area, select **Access tokens → Add**.
2. Fill in the following fields and click **Save**:

| Field | Value |
|-------|-------|
| **User** | Use the magnifying glass icon to search and select the admin |
| **Token** | Specify "redhat" |
| **Token checksum** | Specify "redhat" |
| **Id token** | Leave as it is |
| **Application** | Select **Ansible Lightspeed for VS Code** |
| **Expires** | Set the desired expiry date and time |
| **Scope** | `read write` |

3. Copy and securely store the token value before saving.

---

## Verification

To confirm the end-to-end remediation flow is active, check the live automation status from the control-plane instead of looking for one-shot bootstrap Jobs:

```bash
CONTROL_PLANE_HOST="$(oc get route control-plane -n ani-runtime -o jsonpath='{.status.ingress[0].host}')"

curl -k "https://${CONTROL_PLANE_HOST}/integrations/status" \
  -H "x-api-key: demo-token" | python3 -m json.tool
```

Expected result:

- `aap.bootstrapped=true`
- `eda.bootstrapped=true`
- every AAP action and callback template shows `template_exists=true`
- every EDA policy activation shows `status=running`

The runtime bootstrap owns the controller-side templates and EDA activations. On a fresh cluster, ArgoCD deploys AAP and the platform services, then the control-plane reconciles the live remediation objects after the AAP APIs are ready.
