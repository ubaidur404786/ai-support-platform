# Decision

Build one Docker image per service, put each model inside its service's image, and run everything locally with Docker Compose.

## Context

After v13 the platform is four processes plus PostgreSQL: the API, the worker, the model service and the embedding service. They were started by hand in four terminals, and nothing restarted one that died.

The only Dockerfile was the v0 one. It ran the API alone, and its image would have included llama.cpp and fastembed, which the API no longer uses.

## Options

1. **One image with all the code and every library**, with a different command per container. It is simple, but every container would carry about 1.5 GB of libraries and models it never uses.
2. **One image per service, with the models downloaded when the container starts.** The images are small, but every new container needs network access and waits for a 1.1 GB download.
3. **One image per service, with the models downloaded while the image is built.** This is the option we chose.

## Decision

Option 3, with four images:

| Image | Contains | Runs |
|---|---|---|
| `support-api` | API code, no model libraries, the trained ticket classifier | the API, the worker and the migration (three commands) |
| `support-embedding` | fastembed and the embedding model (~90 MB) | `app.embedding_service.main` |
| `support-model` | llama.cpp and Qwen2.5-1.5B (1.1 GB) | `app.model_service.main` |
| `support-db` | PostgreSQL 17 and pgvector (since v9) | the database |

- Each image installs only its own list of libraries, from `docker/<service>/requirements.txt`. The root `requirements.txt` is used as a pip constraints file, so the versions stay exactly the ones we tested.
- The API and the worker share one image because they share all their code. Splitting them would mean two identical images.

## Why?

- A pulled image runs anywhere with no extra steps: Compose today, Kubernetes in v16, and a fresh AWS machine in v18.
- A model is part of the service's behaviour. Putting it in the image means "image tag X" names exactly one model, which matters for rollback.
- The model download is its own layer, placed before the code is copied. Changing the code rebuilds in seconds instead of downloading 1.1 GB again.

## Trade-offs

- **Gained:**
  - one command starts everything (`docker compose up -d`)
  - a container that crashes is restarted
  - the start order is enforced (database → migration → services → API)
- **Lost:** the model image is large (see the v14 measurements), and pushing or pulling it takes minutes.
- The services share one settings class. So the signing key moved from "required by Settings" to "checked when the API starts" (`create_app`), and the model services can start without one.

## Future Trigger

- **Several model versions to deploy side by side:** keep the model outside the image, on a volume or in object storage, and choose it with a setting.
- **The model image is too slow to pull on new machines:** use a pre-pulled cache on the machines, or a smaller model.
