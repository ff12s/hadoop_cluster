# syntax=docker/dockerfile:1.6

# =============================================================================
# Stage 1: downloader — скачивает, стрипает Spark и кладёт OpenLineage jar.
# Результат складывается в фиксированный путь /spark-dist, чтобы COPY в final
# мог использовать литеральный путь и копировать ТОЛЬКО Spark (не всё /opt).
# =============================================================================
FROM hadoop-cluster-base:latest AS downloader

ARG SPARK_VERSION
ARG OPENLINEAGE_VERSION
ARG SCALA_VERSION

USER root

RUN SCALA_SHORT_VERSION=$(echo ${SCALA_VERSION} | cut -d. -f1,2) && \
    aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o spark.tgz \
        https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop3-scala${SCALA_SHORT_VERSION}.tgz && \
    mkdir -p /spark-dist && \
    tar -xzf /tmp/spark.tgz -C /spark-dist --strip-components=1 && \
    rm -f /tmp/spark.tgz && \
    rm -rf /spark-dist/examples /spark-dist/data /spark-dist/R \
           /spark-dist/kubernetes /spark-dist/python/docs && \
    aria2c -x 16 -s 16 --file-allocation=none -d /spark-dist/jars -o openlineage-spark_${SCALA_SHORT_VERSION}-${OPENLINEAGE_VERSION}.jar \
        https://repo1.maven.org/maven2/io/openlineage/openlineage-spark_${SCALA_SHORT_VERSION}/${OPENLINEAGE_VERSION}/openlineage-spark_${SCALA_SHORT_VERSION}-${OPENLINEAGE_VERSION}.jar && \
    mkdir -p /spark-dist/work /spark-dist/logs && \
    chown -R hadoop:hadoop /spark-dist

# =============================================================================
# Stage 2: final
# =============================================================================
FROM hadoop-cluster-base:latest

ARG SPARK_VERSION
ARG OPENLINEAGE_VERSION
ARG SCALA_VERSION

ENV SPARK_VERSION=${SPARK_VERSION}
ENV SPARK_HOME=/opt/spark
ENV OPENLINEAGE_VERSION=${OPENLINEAGE_VERSION}
ENV PATH=$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin

USER root

# Один чистый слой только со Spark — каталоги hadoop/scala/python наследуются от base без копирования
COPY --from=downloader --chown=hadoop:hadoop /spark-dist /opt/spark

# Конфигурация Spark
COPY config/ /opt/spark/conf/
RUN find /opt/spark/conf -name "*.xml" -o -name "*.sh" -o -name "*.properties" -o -name "*.conf" | xargs dos2unix

# Скрипты
COPY scripts/ /opt/scripts/
RUN find /opt/scripts -name "*.sh" -type f -exec dos2unix {} \; && \
    chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]
