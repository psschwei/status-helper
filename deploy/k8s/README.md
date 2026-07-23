# Deploying to Kubernetes

Manifests to run the Engineering Status Assistant on a Kubernetes cluster.

## What's here

| File | Purpose |
|------|---------|
| `namespace.yaml` | The `status-assistant` namespace everything lives in. |
| `secret.example.yaml` | **Template** for `GITHUB_TOKEN` / `LLM_API_KEY`. Don't commit a filled-in copy. |
| `configmap.yaml` | Non-secret env settings **and** the `repos.toml` / `engineers.toml` / `scrum.toml` config files. |
| `pvc.yaml` | Persistent volume for the SQLite database. |
| `deployment.yaml` | The app (1 replica, `Recreate` strategy, non-root, probes on `/`). |
| `service.yaml` | ClusterIP Service (port 80 → 8000). |
| `ingress.yaml` | Optional external access (edit the host). |
| `kustomization.yaml` | Applies everything except the Secret in one shot. |

## Design notes

- **Single replica + `Recreate` + `ReadWriteOnce` PVC.** SQLite is a single-writer store,
  so the app scales *up* (bigger pod), not *out*. The DB is only a cache of GitHub state —
  safe to delete and re-sync — but it's persisted so it survives restarts and reschedules.
- **Config as files.** The watched repos/engineers/scrum schedule are mounted from the
  ConfigMap at `/config/*.toml`; change them by editing `configmap.yaml` and restarting the
  deployment — no image rebuild.
- **Secrets stay out of git.** The real Secret is created separately (below); only a
  template is committed.

## 1. Build and push the image

```sh
# From the repo root. Use your registry / tag.
export IMAGE=ghcr.io/your-org/status-assistant:v0.1.0
docker build -t "$IMAGE" .
docker push "$IMAGE"
```

## 2. Create the namespace and the Secret

```sh
kubectl apply -f deploy/k8s/namespace.yaml

# Create the Secret imperatively (preferred — nothing sensitive hits disk/git).
# Drop --from-literal=LLM_API_KEY=... to run without AI summaries.
kubectl -n status-assistant create secret generic status-assistant-secrets \
  --from-literal=GITHUB_TOKEN=ghp_your_token_here \
  --from-literal=LLM_API_KEY=sk-your_key_here
```

## 3. Edit config for your environment

- `configmap.yaml` — set `GITHUB_BASE_URL` (GitHub.com vs. Enterprise Server), `LLM_BASE_URL`
  / `LLM_MODEL`, and the `repos.toml` / `engineers.toml` / `scrum.toml` contents.
- `ingress.yaml` — set your `host` (or delete the file and use `port-forward`).
- Image reference — either edit `image:` in `deployment.yaml`, or set it via kustomize:

  ```sh
  cd deploy/k8s
  kustomize edit set image ghcr.io/your-org/status-assistant="$IMAGE"
  ```

## 4. Apply

```sh
# Everything except the Secret (created in step 2):
kubectl apply -k deploy/k8s

# ...or apply files individually:
kubectl apply -f deploy/k8s/configmap.yaml \
              -f deploy/k8s/pvc.yaml \
              -f deploy/k8s/deployment.yaml \
              -f deploy/k8s/service.yaml \
              -f deploy/k8s/ingress.yaml
```

## 5. Verify

```sh
kubectl -n status-assistant rollout status deploy/status-assistant
kubectl -n status-assistant get pods

# No Ingress? Reach it locally:
kubectl -n status-assistant port-forward svc/status-assistant 8080:80
# then open http://localhost:8080
```

## Syncing GitHub data

Table creation happens automatically on startup; ingestion is triggered from the API/UI as
in local dev. After changing `configmap.yaml`, roll the deployment so the new config is
picked up:

```sh
kubectl apply -f deploy/k8s/configmap.yaml
kubectl -n status-assistant rollout restart deploy/status-assistant
```
