# Version

**v15-ci-docker-hub**: GitHub Actions tests every push. On `main` it also builds the service images and pushes them to Docker Hub.

## Problem

After v14 every image was built on one laptop, by hand.

- Nothing stopped someone from building and shipping an image from code whose tests fail.
- No other machine could run the system. A server on AWS (v18) has nowhere to pull `support-api` from.
- The tests ran only when someone remembered to run them, on a laptop where a full run takes 5–9 minutes.

## Current Architecture

Five images exist only in this laptop's Docker: `support-api`, `support-embedding`, `support-model`, `ai-support-db` and, from v17, `support-web`. The tests run locally.

## Why the Old Design Is Not Enough

- **"Tested" and "deployed" are two separate, manual steps.** Nothing links them.
- **A deployment cannot say which code it runs.** Every image is just `latest`.
- **The model image is about 1.5 GB of layers.** Pushing it from a home connection takes a long time.

## Solution

`.github/workflows/ci.yml` (ADR-036) has two jobs:

1. **`test`** runs on every push to `main` or to a `v*` branch, and on pull requests.
   - It starts a throwaway `pgvector/pgvector:pg17` database as a service container.
   - It installs `requirements.txt`, trains the ticket classifier and runs `pytest`.
   - It does not download the 1.1 GB answer model, so the two tests that need the real model are skipped. Every other test runs, including the ones with the real embedding model.
2. **`images`** runs only after `test` passes, and only on a push to `main`.
   - A matrix builds the images, one parallel job each.
   - Each image is pushed to Docker Hub as `<user>/<image>:latest` and as `<user>/<image>:<commit id>`.
   - Layers are cached in GitHub's cache.

It needs two repository secrets, `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. The token must have **Read & Write** access.

## Why This Solution?

- **GitHub Actions** is free for public repositories and already lives next to the code.
- **Docker Hub** is free for public images, and it is the registry every Kubernetes cluster can pull from with no setup.
- **Two tags:** `latest` for trying things out, and the commit id so that a deployment can name an exact version and roll back to it.
- **Simpler alternative:** push from the laptop with `docker push`. It is free, but nothing ties the image to a passing test run.

## New Trade-offs

- **CI does not test the real answer model.** It is too big to download on every push. Those two tests still only run on the laptop.
- **The Docker Hub token is a repository secret.** Anyone with admin rights on the repository can use it.
- **Every push to `main` rebuilds five images.** Caching makes unchanged layers fast, but the model image still has to be pushed whenever its layers change.

## What Changed

| File | Change |
|---|---|
| `.github/workflows/ci.yml` | new: `test` and `images` jobs |
| `app/core/config.py` | version 0.15.0 |

## How to Test

1. In the repository's **Settings → Secrets and variables → Actions**, add `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`. Create the token on Docker Hub with **Read & Write** access.
2. Push a commit, then:

```bash
gh run list --limit 3
```

```bash
gh run watch
```

## Expected Result

- **On a branch:** `test` ✓, and `images` is skipped.
- **On `main`:** `test` ✓, then one `images (...)` job per image ✓. On hub.docker.com the repositories `support-api`, `support-embedding`, `support-model` and `support-db` show the tags `latest` and the commit id.

## Measurements

From the first runs, on 2026-10-27:

| Run | `test` | `images` |
|---|---|---|
| `v15-ci-docker-hub` branch | ✓ (whole run 2 min 4 s) | skipped, as intended |
| `main` | ✓ **2 min 35 s** | ✗ all 4: `401 Unauthorized: access token has insufficient scopes` |

- The tests take **2 min 35 s** on GitHub's runner, against 5.5 minutes on the laptop. The runner has no other apps open, and CI skips the real answer-model tests.
- **The image push failed.** The first Docker Hub token was created read-only. Docker Hub accepted the login, but refused the push. The same token also failed a `docker push` from the laptop.
- **What fixes it:** a new token with Read & Write access, saved in the `DOCKERHUB_TOKEN` secret. Then run the workflow again with `gh run rerun --failed`.

## Interview Explanation

> "I didn't want an image to exist unless its commit passed the tests, and I needed a registry the cloud machine could pull from. A GitHub Actions workflow runs the test suite against a throwaway pgvector database on every push. Only on main, and only if the tests pass, does it build each service's image and push it to Docker Hub with two tags: latest, and the commit id so a deployment can pin and roll back. The first run found a real configuration mistake: the registry token was read-only, so the login worked but the push was refused. The fix was a token with write scope, not a code change."

## Next Possible Limitation

- **Images are in a registry** (once the token is fixed), **but everything still runs on one machine with one API copy** → **v16-kubernetes**.
- **The real answer model is not exercised in CI.**
