# PostgreSQL 17 (the same Alpine image as before) plus the pgvector extension.
#
# Why build our own image instead of using pgvector/pgvector:pg17?
# That image is based on Debian, ours on Alpine. The two sort text differently
# (different C libraries), so pointing a Debian server at the existing data
# volume could silently corrupt every index on a text column (ADR-026).
# Adding the extension to the image we already run keeps the data as it is.

FROM postgres:17-alpine

ARG PGVECTOR_VERSION=0.8.6

# Download pgvector, compile it, install it, then remove the build tools so the
# image stays small.
#   with_llvm=no  - skip the optional JIT files (they need a matching clang)
#   OPTFLAGS=""   - do not tune for this CPU, so the image runs on any x86-64
RUN apk add --no-cache --virtual .build-deps build-base git \
    && git clone --branch v${PGVECTOR_VERSION} --depth 1 https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && make with_llvm=no OPTFLAGS="" \
    && make install with_llvm=no \
    && cd / \
    && rm -rf /tmp/pgvector \
    && apk del .build-deps
