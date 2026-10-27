# Decision

Use GitHub Actions to test every push. On `main`, also build the five images and push them to Docker Hub, tagged `latest` and with the commit id.

## Context

After v14 the images were built on one laptop, by hand. To deploy anywhere else (Kubernetes on AWS, v18), the images must be in a registry that the server can pull from.

There was also nothing to stop an untested commit from becoming an image.

## Options

1. **Build and push by hand from the laptop.** It is free and simple, but nothing stops untested code from being pushed, and the 1.5 GB model image goes over a home connection.
2. **GitHub Actions and Docker Hub.** Both are free: Actions for a public repository, and Docker Hub for public images.
3. **GitHub Actions and GitHub Container Registry or AWS ECR.** ECR costs money after the free tier. GHCR would work just as well; we chose Docker Hub because it is the registry most people recognise.

## Decision

Option 2. The workflow is in `.github/workflows/ci.yml`:

1. **`test`** runs on every push and pull request. It starts a throwaway PostgreSQL with pgvector, trains the ticket classifier and runs `pytest`. The real 1.1 GB answer model is not downloaded, so the tests that need it are skipped. Every other test uses a fake model.
2. **`images`** runs only after `test` passes, and only on `main`. It builds `support-api`, `support-embedding`, `support-model`, `support-db` and `support-web`.
   - Each image is pushed twice: as `:latest` and as `:<commit id>`.
   - Layers are cached between runs.

## Why?

- An image only exists if its commit passed the tests.
- The commit-id tag means a deployment can name exactly which code it runs, and roll back by naming an older id.

## Trade-offs

- **Gained:** repeatable builds, tested images, and a free public registry.
- **Lost:** the CI tests do not cover the real answer model, because it is too big for every push. Those tests still run on the laptop.
- The Docker Hub token is a repository secret. Anyone with admin rights to the repository can use it.

## Future Trigger

- **A private project:** use a private registry, such as ECR, or GHCR with private images.
- **Several environments, such as staging and production:** deploy by commit id from CI, instead of pulling `latest` by hand.
