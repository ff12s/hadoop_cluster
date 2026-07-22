#!/usr/bin/env bash
# e2e-проверка кастомного namespace-резолвера на локальном стенде.
# Собирает jar, монтирует его в jupyter, прогоняет lineage-ноутбук с резолвером,
# включённым ТОЛЬКО на время прогона (per-run SPARK_CONF_DIR — коммитнутый
# spark-defaults.conf резолвер не активирует, чтобы обычные джобы стенда не
# зависели от наличия jar). Проверяет, что резолвер загрузился и Marquez остался
# цел. Пропускается, если стек не поднят.
set -uo pipefail

RESOLVER_DIR="$(cd "$(dirname "$0")/../../openlineage-namespace-resolver" && pwd)"
STAND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
JAR_SRC="$RESOLVER_DIR/target/openlineage-namespace-resolver-0.1.0.jar"
JAR_DST="$STAND_DIR/spark/jars/openlineage-namespace-resolver.jar"
MARQUEZ_NS="http://localhost:5000/api/v1/namespaces"

echo "== 1) Сборка jar =="
( cd "$RESOLVER_DIR" && bash mvnd.sh -q -DskipTests package ) || { echo "СБОРКА УПАЛА"; exit 1; }
cp "$JAR_SRC" "$JAR_DST" || { echo "КОПИРОВАНИЕ JAR УПАЛО (нет собранного jar?)"; exit 1; }
echo "jar -> $JAR_DST"

echo "== 2) Проверка готовности стенда =="
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$MARQUEZ_NS")
if [ "$code" != "200" ]; then
  echo "SKIP: Marquez недоступен ($MARQUEZ_NS -> $code). Подними стенд (start-cluster.bat) и повтори."
  exit 0
fi

echo "== 3) Перезапуск jupyter с примонтированным jar =="
( cd "$STAND_DIR" && docker compose up -d --force-recreate jupyter ) || { echo "ПЕРЕЗАПУСК УПАЛ"; exit 1; }

echo "== 3.1) Ожидание готовности jupyter =="
for i in $(seq 1 12); do
  jc=$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 http://localhost:8888/ 2>/dev/null)
  if [ "$jc" != "000" ]; then echo "jupyter готов (http $jc)"; break; fi
  if [ "$i" = "12" ]; then echo "jupyter не поднялся за отведённое время"; exit 1; fi
  sleep 5
done

echo "== 4) Прогон lineage-ноутбука с резолвером, включённым только на этот прогон =="
# Резолвер активируется через per-run SPARK_CONF_DIR: копируем штатный conf,
# дописываем строку резолвера туда, коммитнутый spark-defaults.conf не трогаем.
docker exec hadoop-jupyter bash -lc '
  set -e
  mkdir -p /tmp/olconf
  cp /opt/spark/conf/* /tmp/olconf/ 2>/dev/null || true
  echo "spark.openlineage.dataset.namespaceResolvers.default.type normalize" >> /tmp/olconf/spark-defaults.conf
  SPARK_CONF_DIR=/tmp/olconf jupyter nbconvert --to notebook --execute --output /tmp/ns_out.ipynb /notebooks/lineage/00_setup.ipynb
' || { echo "ПРОГОН НОУТБУКА УПАЛ (нужен поднятый YARN/HDFS)"; exit 1; }

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
