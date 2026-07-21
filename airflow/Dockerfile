# syntax=docker/dockerfile:1.6

ARG AIRFLOW_VERSION

# =============================================================================
# Донорские стейджи: готовые дистрибутивы из образов стенда, без повторной загрузки.
# =============================================================================
FROM hadoop-cluster-spark:latest AS sparkdist
FROM hadoop-cluster-base:latest AS hadoopdist

# =============================================================================
# Финальный образ. Python 3.10 пинуется явно: безсуффиксный тег даёт
# «новейший Python на момент релиза» — значение неоднозначно.
# =============================================================================
FROM apache/airflow:${AIRFLOW_VERSION}-python3.10

ARG AIRFLOW_VERSION
ENV AIRFLOW_VERSION=${AIRFLOW_VERSION}

USER root

# Java 11: в debian-базе образа Airflow нет openjdk-8, Spark 3.5 поддерживает 8/11/17
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-11-jre-headless \
        curl \
        procps \
    && rm -rf /var/lib/apt/lists/*

COPY --from=sparkdist --chown=airflow:root /opt/spark /opt/spark
COPY --from=hadoopdist --chown=airflow:root /opt/hadoop /opt/hadoop

# Джоба Pi берётся из образа Spark — не дублируем её исходник
COPY --from=sparkdist --chown=airflow:root /opt/scripts/pyspark_pi.py /opt/airflow/jobs/pyspark_pi.py
COPY --chown=airflow:root jobs/ /opt/airflow/jobs/
COPY --chown=airflow:root scripts/ /opt/airflow/scripts/

# chmod через -exec, а не отдельным *.sh-глобом: в Task 1 каталог scripts/
# пуст (только .gitkeep) — глоб без совпадений упал бы с "No such file or directory"
RUN find /opt/airflow/scripts -name "*.sh" -type f -exec sed -i 's/\r$//' {} \; -exec chmod +x {} \;

ENV JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
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

USER airflow

# Версия провайдера выбирается constraints-файлом соответствующей версии Airflow
RUN pip install --no-cache-dir "apache-airflow-providers-apache-spark" \
        --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-3.10.txt"
