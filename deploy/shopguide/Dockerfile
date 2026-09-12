# Official linux/amd64 manifest selected from a recorded official multi-platform index.
# Build web/dist first; only allowlisted source files enter the build context.
ARG PYTHON_IMAGE=python:3.12-slim-trixie@sha256:2fe5997d249a808b8eeea52c58a1dbffbba28754dc11699ef5c029f2d818ce79
FROM ${PYTHON_IMAGE} AS build
WORKDIR /app
ENV UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.11.15
COPY pyproject.toml uv.lock README.md LICENSE /app/
COPY shopguide /app/shopguide
RUN uv sync --locked --no-dev --extra shopguide --extra storage --no-editable

FROM ${PYTHON_IMAGE} AS runtime
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Security updates come from the signed distribution repositories. Record final image/SBOM.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && printf 'Acquire::Retries "1"; Acquire::https::Timeout "30"; Acquire::http::Timeout "30"; APT::Update::Error-Mode "any";\n' > /etc/apt/apt.conf.d/99shopguide-network \
    && apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip setuptools
COPY --from=build /app/.venv /app/.venv
COPY web/dist /app/web/dist
RUN groupadd --gid 10001 shopguide && useradd --uid 10001 --gid 10001 --no-create-home shopguide \
    && mkdir /data /backups && chown 10001:10001 /data /backups
USER 10001:10001
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8484/health/live',timeout=3)"]
ENTRYPOINT ["/app/.venv/bin/shopguide"]
CMD ["serve", "--root", "/data", "--web-dist", "/app/web/dist", "--demo", "--fake", "--seed-demo", "--log-level", "warning"]
