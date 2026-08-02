#!/usr/bin/env bash
# Dev-обёртка Maven. Предпочитает хостовый mvn (если он в PATH); иначе гоняет
# Maven в Docker, чтобы не требовать хостовый JDK/Maven. Кэш зависимостей
# Docker-режима — в локальном .m2 (в .gitignore).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
if command -v mvn >/dev/null 2>&1; then
  exec mvn "$@"
fi
export MSYS_NO_PATHCONV=1
mkdir -p "$DIR/.m2"
exec docker run --rm \
  -v "$DIR":/w -v "$DIR/.m2":/root/.m2 -w /w \
  maven:3.9-eclipse-temurin-8 mvn "$@"
