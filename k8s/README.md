# Kubernetes files

Each part of the platform has one file:

| File | What it runs | Copies | Reachable from |
|---|---|---|---|
| `db.yaml` | PostgreSQL + pgvector (StatefulSet, 2 GB disk) | 1 | inside only |
| `redis.yaml` | shared rate-limit counters | 1 | inside only |
| `migrate.yaml` | `alembic upgrade head`, once (Job) | – | – |
| `model-services.yaml` | answer model + embedding model | 1 + 1 | inside only |
| `api.yaml` | the API, and the worker | 3 + 1 | inside only |
| `web.yaml` | the React app + nginx (v17) | 1 | **outside, port 30080** |
| `config.yaml` | shared settings (ConfigMap) | | |
| `secret.example.yaml` | what the Secret must contain (example only) | | |

## Run it on a laptop: k3s inside Docker

k3s is a small but complete Kubernetes, made by Rancher. It is the same one that runs on the AWS machine in v18, so the commands and the files are the same in both places.

**1. Start a one-node cluster.** It runs as a single Docker container. Port 30080 is the web app.

```bash
docker run -d --name k3s --privileged --restart unless-stopped -p 6444:6443 -p 30080:30080 rancher/k3s:v1.31.4-k3s1 server --disable traefik --disable metrics-server
```

**2. Point kubectl at it.** Linux/WSL:

```bash
docker exec k3s cat /etc/rancher/k3s/k3s.yaml | sed 's/6443/6444/' > ~/.kube/k3s-local.yaml
export KUBECONFIG=~/.kube/k3s-local.yaml
kubectl get nodes
```

PowerShell:

```powershell
docker exec k3s cat /etc/rancher/k3s/k3s.yaml | % { $_ -replace '6443','6444' } | Set-Content -Encoding ascii $HOME\.kube\k3s-local.yaml
$env:KUBECONFIG="$HOME\.kube\k3s-local.yaml"
kubectl get nodes
```

**3. Copy the locally built images into the cluster.** k3s has its own image store, separate from Docker's.

```bash
docker compose build
docker tag ai-support-db:pg17-pgvector support-db:latest
for image in support-api support-embedding support-model support-db support-web; do
  docker save $image:latest | docker exec -i k3s ctr images import -
done
```

**4. Create the Secret, then start everything.**

```bash
kubectl create secret generic app-secrets \
  --from-literal=POSTGRES_PASSWORD=choose-a-password \
  --from-literal=DATABASE_URL=postgresql+psycopg://support:choose-a-password@db:5432/support_platform \
  --from-literal=JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

```bash
kubectl apply -k k8s
```

```bash
kubectl get pods -w
```

After 2–4 minutes every pod is `Running` with `1/1` ready, and `migrate` shows `Completed`. Open http://localhost:30080, or run:

```bash
python scripts/smoke_test.py http://localhost:30080/api
```

## Useful commands

```bash
kubectl scale deployment api --replicas=5     # more API copies
kubectl logs -f deployment/model-service      # follow one service's log
kubectl delete pod -l app=api --wait=false    # kill API copies; new ones replace them
kubectl rollout restart deployment api        # replace the copies one by one
kubectl delete -k k8s                         # remove everything (the database disk too)
docker rm -f k3s                              # remove the whole cluster
```

## Why not Docker Desktop's built-in Kubernetes?

It was switched on, but on this 8 GB laptop it never answered (`Unable to connect to the server: EOF`). k3s started in under 40 seconds, and it is the same cluster type we use on AWS.
