# MonsoonGrid runtime image (design §9.2). Build from the REPO ROOT:
#   docker build -f deploy/Dockerfile -t monsoongrid:local \
#     --build-arg SOURCE_REVISION=<rev> --build-arg SOURCE_TREE_DIGEST=<digest> .
#
# Python stages use the same base so the ABI matches. The base tag is pinned
# by DIGEST on the first successful build (deploy/verify_image.ps1 captures it);
# until then it names a floating tag (residual risk R-16).

FROM node:22-bookworm-slim AS ui-builder
WORKDIR /ui
COPY src/monsoon_grid/ui/package.json src/monsoon_grid/ui/package-lock.json ./
RUN npm ci
COPY src/monsoon_grid/ui/ ./
RUN npm run build

# ---------- Python dependency builder ----------
FROM python:3.13-slim-bookworm AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY deploy/requirements-linux.txt .
RUN python -m pip install --no-cache-dir --upgrade "pip==25.2" \
 && python -m pip install --no-cache-dir --target /opt/monsoon/site-packages \
        -r requirements-linux.txt \
 && find /opt/monsoon/site-packages -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find /opt/monsoon/site-packages -name 'tests' -type d -prune -exec rm -rf {} +

# ---------- stage 2: runtime ----------
FROM python:3.13-slim-bookworm
ARG SOURCE_REVISION=unknown
ARG SOURCE_TREE_DIGEST=unknown
LABEL org.opencontainers.image.title="MonsoonGrid" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      monsoongrid.source_tree_digest="${SOURCE_TREE_DIGEST}" \
      monsoongrid.scope="SIMULATION/REPLAY/NO-LIVE-CONTROL"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0 \
    PYTHONPATH=/opt/monsoon/src:/opt/monsoon/site-packages \
    MONSOON_GRID_RUNTIME_ROOT=/var/monsoon/runtime \
    MONSOON_GRID_TRUST_KEYRING=/opt/monsoon/trust/verification-keyring.json \
    SOURCE_REVISION=${SOURCE_REVISION} \
    SOURCE_TREE_DIGEST=${SOURCE_TREE_DIGEST} \
    PORT=8080
RUN groupadd --gid 10001 monsoon \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin monsoon \
 && mkdir -p /var/monsoon/runtime && chown -R 10001:10001 /var/monsoon
COPY --from=builder /opt/monsoon/site-packages /opt/monsoon/site-packages
COPY --chown=10001:10001 src/           /opt/monsoon/src/
COPY --from=ui-builder --chown=10001:10001 /ui/dist/ /opt/monsoon/src/monsoon_grid/ui/dist/
COPY --chown=10001:10001 experiments/results/ /opt/monsoon/experiments/results/
COPY --chown=10001:10001 fixtures/      /opt/monsoon/fixtures/
COPY --chown=10001:10001 trust/         /opt/monsoon/trust/
COPY --chown=10001:10001 pyproject.toml /opt/monsoon/
COPY --chown=10001:10001 deploy/__init__.py deploy/asgi.py deploy/cadence.py deploy/source_revision.py deploy/entrypoint.sh deploy/uvicorn-logging.json /opt/monsoon/deploy/
RUN chmod 0555 /opt/monsoon/deploy/entrypoint.sh
USER 10001:10001
WORKDIR /opt/monsoon
# Bake the signed demo replay evidence into the image so the service starts
# instantly on Cloud Run (no slow MILP solve during the startup probe window).
# This is the same deterministic demo the local wrapper runs; SIMULATION/REPLAY only.
RUN MONSOON_GRID_ENV=development \
    MONSOON_GRID_AUTH_MODE=local_dev \
    MONSOON_GRID_DEV_BEARER_TOKEN=build-time-demo \
    MONSOON_GRID_RUNTIME_ROOT=/var/monsoon/runtime \
    MONSOON_GRID_TRUST_KEYRING=/opt/monsoon/trust/verification-keyring.json \
    MONSOON_GRID_SIGNING_KEY=/opt/monsoon/trust/demo-signing-seed.hex \
    MONSOON_GRID_TRUST_FINGERPRINT=56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c \
    python -m monsoon_grid demo --seed 20260701 --policy balanced --deadline 285
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python","-c","import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/_ops/healthz',timeout=4).status==200 else 1)"]
ENTRYPOINT ["/opt/monsoon/deploy/entrypoint.sh"]
