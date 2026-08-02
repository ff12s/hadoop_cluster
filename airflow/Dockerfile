# syntax=docker/dockerfile:1.6

# Дефолт нужен для сборки без compose: иначе тег развернулся бы в ":-python3.10"
ARG AIRFLOW_VERSION=2.6.3

# =============================================================================
# Донорские стейджи: готовые дистрибутивы из образов стенда, без повторной загрузки.
# =============================================================================
FROM hadoop-cluster-spark:latest AS sparkdist
FROM hadoop-cluster-base:latest AS hadoopdist

# =============================================================================
# Финальный образ. Python 3.10 пинуется явно: безсуффиксный тег даёт
# «новейший Python на момент релиза» — значение неоднозначно. Суффикс -python3.10
# менять только вместе с constraints-3.10.txt в pip-шаге ниже.
# =============================================================================
FROM apache/airflow:${AIRFLOW_VERSION}-python3.10

ARG AIRFLOW_VERSION=2.6.3
ENV AIRFLOW_VERSION=${AIRFLOW_VERSION}

# Срез реквайрментов выбирает вариант образа: requirements_slim.txt (2.6.3) либо
# requirements_cloud_slim.txt (2.10.2). Ставится без --constraint: сам файл и есть
# источник пинов, constraints-файл Airflow перебил бы их.
ARG AIRFLOW_REQUIREMENTS=requirements_slim.txt

USER root

# Java: в debian-базе образа Airflow нет openjdk-8, Spark 3.5 поддерживает 8/11/17.
# Пакет openjdk-11-jre-headless есть в bullseye (база 2.6.3), но отсутствует в
# bookworm (база 2.10.2, там только openjdk-17) — падаем на 17 вторым вариантом.
RUN apt-get update \
    && (apt-get install -y --no-install-recommends openjdk-11-jre-headless \
        || apt-get install -y --no-install-recommends openjdk-17-jre-headless) \
    && apt-get install -y --no-install-recommends curl procps \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s "$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")" /opt/java-current

COPY --from=sparkdist --chown=airflow:root /opt/spark /opt/spark
COPY --from=hadoopdist --chown=airflow:root /opt/hadoop /opt/hadoop

# OpenLineage-jar удаляем из образа намеренно: под YARN (spark.yarn.jars не задан)
# spark-submit заливает клиентский $SPARK_HOME/jars как classpath драйвера, поэтому
# запечённый jar скрыл бы прод-поведение — на проде jar лежит в HDFS, а не под
# SPARK_HOME. Airflow-джобы тащат его из HDFS через --jars (см. cluster policy
# airflow/config/ol_policy и Variable openlineage_config, поле openlineage_jar).
# Так стенд честно повторяет прод.
RUN rm -f /opt/spark/jars/openlineage-spark_*.jar

# Джобы в образ не копируются: compose монтирует ./airflow/jobs в /opt/airflow/jobs
# и каталог-маунт всё равно перекрыл бы встроенную копию.
COPY --chown=airflow:root scripts/ /opt/airflow/scripts/

# find -exec, а не *.sh-глоб: устойчиво к пустому каталогу
RUN find /opt/airflow/scripts -name "*.sh" -type f -exec sed -i 's/\r$//' {} \; -exec chmod +x {} \;

ENV JAVA_HOME=/opt/java-current
ENV SPARK_HOME=/opt/spark
ENV HADOOP_HOME=/opt/hadoop
ENV HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop
ENV PATH=$JAVA_HOME/bin:$SPARK_HOME/bin:$HADOOP_HOME/bin:$PATH

# /etc/profile у Debian безусловно переписывает PATH для непривилегированных пользователей —
# ENV PATH из образа теряется в login-шеллах (bash -l/-lc), хотя корректно применяется к
# exec-процессам контейнера (тем, что реально запускает SparkSubmitOperator). Восстанавливаем
# те же пути через profile.d, чтобы `docker exec ... bash -l` тоже видел spark-submit/yarn/java.
RUN { \
        echo "export JAVA_HOME=$JAVA_HOME"; \
        echo "export SPARK_HOME=$SPARK_HOME"; \
        echo "export HADOOP_HOME=$HADOOP_HOME"; \
        echo "export HADOOP_CONF_DIR=$HADOOP_CONF_DIR"; \
        echo 'export PATH="$JAVA_HOME/bin:$SPARK_HOME/bin:$HADOOP_HOME/bin:$PATH"'; \
    } > /etc/profile.d/hadoop-cluster-path.sh

COPY --chown=airflow:root ${AIRFLOW_REQUIREMENTS} /opt/airflow/requirements-active.txt

USER airflow

RUN pip install --no-cache-dir -r /opt/airflow/requirements-active.txt

# pyspark — транзитивная зависимость apache-airflow-providers-apache-spark; в
# рантайме не нужен, так как spark-submit берётся из /opt/spark. Удаляем сразу
# после установки, а не через --no-deps на строке выше: тому слою нужно
# разрешить полное дерево зависимостей самого Airflow.
RUN pip uninstall -y pyspark

# pytest ставится отдельным слоем, а не через requirements.txt: тот описывает
# рантайм стенда, а не тестовый контур. Версия не пинуется явно — её выбирает
# constraints-файл соответствующей версии Airflow (7.4.0 для 2.6.3, 8.3.3 для
# 2.10.2); хардкод одной версии на оба варианта ловит ResolutionImpossible.
# Набор тестов cluster policy запускается так:
# docker exec hadoop-airflow python -m pytest /opt/airflow/config/tests
RUN pip install --no-cache-dir "pytest" \
        --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-3.10.txt"
