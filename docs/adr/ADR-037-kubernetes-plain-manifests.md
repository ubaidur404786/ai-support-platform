# Decision

Describe the system as plain Kubernetes YAML files in `k8s/`, combined with Kustomize. Run them on k3s: inside one Docker container on the laptop (v16), and on a single AWS machine (v18).

## Context

Docker Compose runs everything on one machine, and it cannot run 3 copies of the API behind one name. It has no readiness checks that control which copy gets traffic, and no rolling update.

The memory work in v12 and v13 was done precisely so that the API could run as several small copies while the model runs once.

## Options

1. **Helm charts.** They are powerful, but they add a template language on top of the YAML, which is too much for a first Kubernetes version.
2. **Plain YAML and Kustomize.** Kustomize is built into kubectl (`kubectl apply -k`). This is the option we chose.
3. **Managed Kubernetes (EKS).** It costs about $0.10 an hour for the control plane alone, before any machines.

## Decision

- **One file per part of the system:** `db.yaml`, `redis.yaml`, `migrate.yaml`, `model-services.yaml`, `api.yaml`, `web.yaml`.
- **Settings in a ConfigMap**, and secrets in a Secret created with `kubectl create secret`. The repository only has `secret.example.yaml`.
- **Number of copies:**

  | Part | Copies | Why |
  |---|---|---|
  | API | 3 | It keeps no state, so copies are interchangeable |
  | model service | 1 | Each copy holds its own model and its own queue (ADR-031) |
  | embedding service | 1 | Enough for a laptop |
  | worker | 1 | |
  | db | 1 | A StatefulSet with its own disk |
  | redis | 1 | |

- **Readiness probes** decide when a copy gets traffic. The model services are ready only when `model_loaded` is true. The API's readiness does not depend on the model services (ADR-035, v14 `/health`).
- **Migrations run as a Job**, so three API copies never change the schema at the same time.
- `k8s/aws/kustomization.yaml` reuses every file and only swaps the image names for Docker Hub ones.

## Why?

- Every object is visible in a file someone can read. That suits a learning project and an interview.
- The same files run locally and on AWS, which is the point of using Kubernetes.
- **Why k3s and not Docker Desktop's built-in Kubernetes:** the built-in one was switched on but never answered on this 8 GB laptop (`Unable to connect to the server: EOF`). k3s in a container was ready in under 40 seconds, and it is the same distribution as on AWS.

## Trade-offs

- **Gained:** scaling with one command, automatic restarts, rolling updates, and traffic only to ready copies.
- **Lost:**
  - a lot more YAML than Compose
  - the cluster itself uses memory (the laptop has 8 GB)
  - the database inside the cluster is fine for a demo, but not how production stores data (see the trigger below)

## Future Trigger

- **Real users and data:** move PostgreSQL to a managed database (RDS), and keep only stateless parts in the cluster.
- **Many environments with small differences:** move to Helm, or to more Kustomize overlays.
