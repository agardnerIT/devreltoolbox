FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

RUN rm -f /etc/apt/sources.list.d/yarn.list /etc/apt/sources.list.d/yarn*.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates nodejs npm \
    && rm -rf /var/lib/apt/lists/*
