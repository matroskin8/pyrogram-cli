FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    TGCLI_WORKDIR=/data TGCLI_NONINTERACTIVE=1 HOME=/home/tgcli
RUN groupadd --gid 10001 tgcli && useradd --uid 10001 --gid 10001 --create-home tgcli \
    && mkdir /data /work && chown tgcli:tgcli /data /work
WORKDIR /app
COPY pyproject.toml README.md ./
COPY tgcli ./tgcli
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir . \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001
WORKDIR /work
ENTRYPOINT ["tgcli"]
CMD ["--help"]
