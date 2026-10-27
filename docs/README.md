# How the project grew, version by version

The project started as one small file and grew one step at a time. Each step fixed a problem that the previous step showed, and each one has its own Git branch (`v0-baseline`, `v1-modular-monolith`, …).

The whole story in one picture: [evolution.svg](architecture/evolution.svg)

| Version | The problem it fixed | Diagram | Details |
|---|---|---|---|
| v0 Baseline | Classify a support ticket with a small local model over HTTP | [svg](architecture/v0.svg) | [short](versions/v0-baseline/README.md) · [full](versions/v0-baseline.md) |
| v1 Modular monolith | One big file, hard to change and test | [svg](architecture/v1.svg) | [short](versions/v1-modular-monolith/README.md) · [full](versions/v1-modular-monolith.md) |
| v2 PostgreSQL | Tickets were lost when the server restarted | [svg](architecture/v2.svg) | [short](versions/v2-postgresql/README.md) · [full](versions/v2-postgresql.md) |
| v3 Pagination | Listing 13,000 tickets at once took 2.2 s | [svg](architecture/v3.svg) | [short](versions/v3-pagination/README.md) · [full](versions/v3-pagination.md) |
| v4 Authentication | Anyone could read every ticket | [svg](architecture/v4.svg) | [short](versions/v4-authentication/README.md) · [full](versions/v4-authentication.md) |
| v5 Rate limiting | One user could keep the server busy | [svg](architecture/v5.svg) | [short](versions/v5-rate-limiting/README.md) · [full](versions/v5-rate-limiting.md) |
| v6 Documents | No way to store or search help articles | [svg](architecture/v6.svg) | [short](versions/v6-document-ingestion/README.md) · [full](versions/v6-document-ingestion.md) |
| v7 Background worker | A big PDF blocked the upload request | [svg](architecture/v7.svg) | [short](versions/v7-async-processing/README.md) · [full](versions/v7-async-processing.md) |
| v8 Embeddings | Search missed questions worded differently | [svg](architecture/v8.svg) | [short](versions/v8-embeddings/README.md) · [full](versions/v8-embeddings.md) |
| v9 Vector search | Search by meaning got slow with many documents | [svg](architecture/v9.svg) | [short](versions/v9-vector-search/README.md) · [full](versions/v9-vector-search.md) |
| v10 RAG answers | Search gave passages, never an answer | [svg](architecture/v10.svg) | [short](versions/v10-rag/README.md) · [full](versions/v10-rag.md) |
| v11 Streaming | Users waited ~6 s at a blank screen | [svg](architecture/v11.svg) | [short](versions/v11-answer-streaming/README.md) · [full](versions/v11-answer-streaming.md) |
| v12 Model service | The answer model was copied into every API process | [svg](architecture/v12.svg) | [short](versions/v12-model-service/README.md) · [full](versions/v12-model-service.md) |
| v13 Embedding service | The search model was loaded twice; first search took 21 s | [svg](architecture/v13.svg) | [short](versions/v13-embedding-service/README.md) · [full](versions/v13-embedding-service.md) |
| v14 Containers | Five processes started by hand | [svg](architecture/v14.svg) | [short](versions/v14-containers/README.md) · [full](versions/v14-containers.md) |
| v15 CI + Docker Hub | Images built by hand, not tested | [svg](architecture/v15.svg) | [short](versions/v15-ci-docker-hub/README.md) · [full](versions/v15-ci-docker-hub.md) |
| v16 Kubernetes | One API copy could take the whole system down | [svg](architecture/v16.svg) | [short](versions/v16-kubernetes/README.md) · [full](versions/v16-kubernetes.md) |
| v17 Web app | Only usable with curl | [svg](architecture/v17.svg) | [short](versions/v17-react-frontend/README.md) · [full](versions/v17-react-frontend.md) |

## Decisions

Every important choice (why PostgreSQL, why pgvector, why Kubernetes, …) has a short record in [adr/](adr/): the problem, the options, what was chosen and what it costs.

## Other folders

- [`../tests/README.md`](../tests/README.md): the tests, numbered by version
- [`../k8s/README.md`](../k8s/README.md): running on Kubernetes
- [`../evaluation/`](../evaluation/): question sets and measured results
