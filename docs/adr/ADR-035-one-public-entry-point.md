# Decision

Expose only the web app to the outside. The database, Redis, the model service, the embedding service and (from v17) the API itself are reachable only on the internal network.

## Context

The model and embedding services have no authentication. Anyone who can reach port 8001 can make the answer model write text for them, and use up the 4-place queue.

Until v13 everything ran on one laptop, so this did not matter. With containers, and later a server on the internet (v18), it does.

## Options

1. **Publish every port and add authentication to every service.** That means a lot of new code for services that only our own API ever calls.
2. **Publish one entry point and keep everything else internal.** This is the option we chose.

## Decision

- **In Docker Compose**, only `web` (8080) is published for users. The API's 8000 and the database's 5432 stay published only for development and for the measurement scripts. Model and embedding services are never published.
- **In Kubernetes**, every Service is `ClusterIP` (internal) except `web`, which is a `NodePort` on 30080.
- **nginx in the web image** forwards `/api/...` to `http://api:8000/`. The browser sees one site, so no CORS settings are needed.
- **The API runs uvicorn with `--proxy-headers`**, so the per-address login limit sees the user's address and not nginx's. It trusts that header only because the API cannot be reached except through our own nginx.

## Why?

- It is the simplest real security boundary: the services that cost the most to run are the ones nobody outside can reach.
- Containers and Kubernetes already give us internal name resolution (`model-service`, `db`), so the internal calls need no extra setup.

## Trade-offs

- **Gained:** no new authentication code, and a smaller attack surface.
- **Lost:** everything inside the network trusts everything else. If an attacker gets into one container, they can call the model service directly.
- `--proxy-headers` would be wrong if the API were ever exposed directly, because a caller could fake their address. That is written next to the setting in `docker/api/Dockerfile`.

## Future Trigger

- **Services owned by different teams, or a shared cluster:** add service-to-service authentication (mutual TLS or tokens) and Kubernetes NetworkPolicies.
- **More than one public service:** use an Ingress or a load balancer in front, instead of a NodePort.
