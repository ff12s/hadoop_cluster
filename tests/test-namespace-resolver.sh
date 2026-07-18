#!/usr/bin/env bash
# e2e-проверка кастомного namespace-резолвера на локальном стенде.
# Собирает jar, монтирует его в jupyter, прогоняет lineage-ноутбук, проверяет,
# что резолвер загрузился и Marquez остался цел. Пропускается, если стек не поднят.
set -uo pipefail

RESOLVER_DIR="$(cd "$(dirname "$0")/../../openlineage-namespace-resolver" && pwd)"
STAND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
JAR_SRC="$RESOLVER_DIR/target/openlineage-namespace-resolver-0.1.0.jar"
JAR_DST="$STAND_DIR/spark/jars/openlineage-namespace-resolver.jar"
MARQUEZ_NS="http://localhost:5000/api/v1/namespaces"

echo "== 1) Сборка jar =="
( cd "$RESOLVER_DIR" && bash mvnd.sh -q -DskipTests package ) || { echo "СБОРКА УПАЛА"; exit 1; }
cp "$JAR_SRC" "$JAR_DST"
echo "jar -> $JAR_DST"

echo "== 2) Проверка готовности стенда =="
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$MARQUEZ_NS" || echo 000)
if [ "$code" != "200" ]; then
  echo "SKIP: Marquez недоступен ($MARQUEZ_NS -> $code). Подними стенд (start-cluster.bat) и повтори."
  exit 0
fi

echo "== 3) Перезапуск jupyter с примонтированным jar =="
( cd "$STAND_DIR" && docker compose up -d --force-recreate jupyter ) || { echo "ПЕРЕЗАПУСК УПАЛ"; exit 1; }

echo "== 4) Прогон lineage-ноутбука =="
docker exec hadoop-jupyter bash -lc \
  'jupyter nbconvert --to notebook --execute --output /tmp/ns_out.ipynb /notebooks/lineage/00_setup.ipynb' \
  || { echo "ПРОГОН НОУТБУКА УПАЛ (нужен поднятый YARN/HDFS)"; exit 1; }

echo "== 5) Проверка: резолвер активен в логах драйвера =="
if docker logs hadoop-jupyter 2>&1 | grep -q "NamespaceNormalizer active"; then
  echo "OK: резолвер загружен и собран из SparkConf"
else
  echo "FAIL: строки 'NamespaceNormalizer active' нет в логах — резолвер не подхватился"
  exit 1
fi

echo "== 6) Проверка: Marquez не отравлен (GET /namespaces = 200) =="
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$MARQUEZ_NS")
if [ "$code" = "200" ]; then
  echo "OK: GET /namespaces = 200"
else
  echo "FAIL: GET /namespaces = $code (в namespaces мог попасть невалидный элемент)"
  exit 1
fi

echo "== ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ =="
