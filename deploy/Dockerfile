# Hosted s2s-mcp: one container holds both libraries, the shared data cache,
# and the per-tenant output store. Run behind TLS with an OAuth-capable proxy.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY skills ./skills
RUN uv sync --frozen --no-dev

# Shared acmaddl cache (one CDS account, one download per product/request)
# and per-tenant outputs. Mount both as volumes in production.
ENV ACMADDL_CACHE_DIR=/data/cache \
    S2S_OUTPUT_ROOT=/data/outputs \
    S2S_HOST=0.0.0.0 \
    S2S_PORT=8080
VOLUME ["/data/cache", "/data/outputs"]
EXPOSE 8080

CMD ["uv", "run", "--no-sync", "s2s-mcp", "--transport", "streamable-http", "--stateless"]
