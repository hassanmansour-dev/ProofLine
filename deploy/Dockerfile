# Measured Ops runtime image (C10 deploy proposal). One image runs the inbox and the
# worker; compose picks the command. Public repo: no secret and no real config is baked in
# (config is mounted at /config, secrets arrive as environment variables).
FROM python:3.12-slim-bookworm

# uv: installs the locked workspace, and `uvx` starts stdio MCP servers named in the tools
# config (the Outlook calendar server). Pin the tag you have tested when you build.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=America/Toronto

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 ops

WORKDIR /app
# The workspace members must be present for a locked sync, so the code is copied first.
COPY pyproject.toml uv.lock ./
COPY packages ./packages
RUN uv sync --frozen --no-dev --all-packages --extra inbox --extra postgres --extra tools

# Writable state, owned by the runtime user so named volumes mounted here inherit it:
#   .data / outputs  the inbox's local files and the digests
#   /state           the kill switch file, shared by the inbox and worker containers
#   .outlook-mcp     the calendar server's config and token cache
RUN mkdir -p /app/.data /app/outputs /state /home/ops/.outlook-mcp \
 && chown -R ops:ops /app/.data /app/outputs /state /home/ops/.outlook-mcp \
 && chmod 700 /home/ops/.outlook-mcp
USER ops

# The inbox binds 127.0.0.1 inside the container and is never published: the Tailscale
# container shares its network namespace (compose.yaml).
EXPOSE 8765
CMD ["proofline", "inbox"]
