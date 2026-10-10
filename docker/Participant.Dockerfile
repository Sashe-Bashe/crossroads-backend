# Build only through scripts/container_participant.py --build-image. Its minimal
# temporary context contains public CLI artifacts and binaries, never credentials.
FROM python:3.14-slim
COPY requirements.txt /tmp/requirements.txt
COPY wheels/ /tmp/wheels/
RUN pip install --no-cache-dir -r /tmp/requirements.txt /tmp/wheels/*.whl \
    && rm -rf /tmp/requirements.txt /tmp/wheels \
    && useradd --uid 1000 --create-home participant \
    && mkdir -p /workspace /home/participant/.codex
COPY --chmod=755 codex codex-code-mode-host rg /usr/local/bin/
ENV HOME=/home/participant CODEX_HOME=/home/participant/.codex \
    MARKET_CONFIG=/run/market-profile.json MARKET_PROFILE=default \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
USER 1000:1000
CMD ["codex", "--version"]
