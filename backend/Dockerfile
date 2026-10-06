# Image unique pour l'API et le worker.
#   --build-arg ML=1 (defaut) : torch, InsightFace, NudeNet... pour les workers.
#   --build-arg ML=0          : serveur de coordination (API, watcher, faces cluster, series build) : aucun modele
#                               installable, donc aucune analyse possible sur cette machine, image bien plus legere.
#   --build-arg CLAUDE_CLI=1  : ajoute le CLI Claude Code, pour le VLM via abonnement (CLAUDE_CODE_OAUTH_TOKEN).
# Multi-arch : fonctionne en amd64 (ta machine) et arm64 (DGX Spark, Mac en Docker).
FROM python:3.12-slim-bookworm
ARG ML=1
ARG CLAUDE_CLI=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_CACHE=1 \
    HF_HOME=/data/models/hf

# ML=1 seulement. build-essential : insightface compile une extension Cython. libglib, libgl, libxcb... : opencv
# (nudenet tire la version non headless).
RUN if [ "$ML" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends \
        build-essential libglib2.0-0 libgomp1 libgl1 libxcb1 libxext6 libxrender1 libsm6 \
    && rm -rf /var/lib/apt/lists/*; fi

# Binaire autonome (pas de Node), copie hors de /root pour rester utilisable quel que soit HOME.
ENV DISABLE_AUTOUPDATER=1
RUN if [ "$CLAUDE_CLI" = "1" ]; then apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
        && curl -fsSL https://claude.ai/install.sh | bash \
        && cp -L /root/.local/bin/claude /usr/local/bin/claude && rm -rf /root/.local /root/.claude* \
        && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*; fi

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
# Dependances d'abord (couche stable), code ensuite : un changement de code ne reinstalle pas torch.
COPY pyproject.toml README.md ./
RUN mkdir -p src/photoflow && touch src/photoflow/__init__.py \
    && uv venv /opt/venv \
    && if [ "$ML" = "1" ]; then VIRTUAL_ENV=/opt/venv uv pip install -e ".[ml]"; else VIRTUAL_ENV=/opt/venv uv pip install -e ".[server]"; fi
COPY src ./src
ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000
CMD ["photoflow", "serve"]
