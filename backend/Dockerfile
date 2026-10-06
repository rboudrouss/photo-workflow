# Image unique pour l'API et le worker (le worker charge les modeles, l'API non, sauf pour la
# recherche vectorielle par texte qui charge SigLIP a la premiere requete).
# Multi-arch : fonctionne en amd64 (ta machine) et arm64 (DGX Spark, Mac en Docker).
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    HF_HOME=/data/models/hf

# build-essential : insightface compile une extension Cython. libglib, libgl, libxcb... : opencv (nudenet tire
# la version non headless).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libglib2.0-0 libgomp1 libgl1 libxcb1 libxext6 libxrender1 libsm6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
# Dependances d'abord (couche stable), code ensuite : un changement de code ne reinstalle pas torch.
COPY pyproject.toml README.md ./
RUN mkdir -p src/photoflow && touch src/photoflow/__init__.py \
    && uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install -e ".[ml]"
COPY src ./src
ENV PATH="/opt/venv/bin:$PATH"

EXPOSE 8000
CMD ["photoflow", "serve"]
