# syntax=docker/dockerfile:1.6

# =============================================================================
# Stage 1: downloader — скачивает Kyuubi, делает chown/chmod в discarded-стейдже.
# Главное: /opt/spark здесь НЕ трогается, чтобы не дублировать его в финальный слой.
# =============================================================================
FROM hadoop-cluster-spark:latest AS downloader

ARG KYUUBI_VERSION

USER root

RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o kyuubi.tgz \
        https://archive.apache.org/dist/kyuubi/kyuubi-${KYUUBI_VERSION}/apache-kyuubi-${KYUUBI_VERSION}-bin.tgz && \
    mkdir -p /kyuubi-dist && \
    tar -xzf /tmp/kyuubi.tgz -C /kyuubi-dist --strip-components=1 && \
    rm -f /tmp/kyuubi.tgz && \
    rm -rf /kyuubi-dist/docs && \
    mkdir -p /kyuubi-dist/logs /kyuubi-dist/work /kyuubi-dist/pid && \
    chown -R hadoop:hadoop /kyuubi-dist && \
    chmod -R 775 /kyuubi-dist

# =============================================================================
# Stage 2: final — НЕ касаемся /opt/spark, наследуем как есть от spark-образа.
# =============================================================================
FROM hadoop-cluster-spark:latest

ARG SPARK_VERSION
ARG KYUUBI_VERSION

ENV SPARK_VERSION=${SPARK_VERSION}
ENV KYUUBI_VERSION=${KYUUBI_VERSION}
ENV SPARK_HOME=/opt/spark
ENV KYUUBI_HOME=/opt/kyuubi
ENV PATH=$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin:$KYUUBI_HOME/bin

USER root

# Только Kyuubi — один чистый слой
COPY --from=downloader --chown=hadoop:hadoop /kyuubi-dist /opt/kyuubi

# Конфиги Kyuubi
COPY config/ /opt/kyuubi/conf/
RUN find /opt/kyuubi/conf -name "*.xml" -o -name "*.sh" -o -name "*.properties" -o -name "*.conf" | xargs dos2unix

# Скрипты
COPY scripts/ /opt/scripts/
RUN find /opt/scripts -name "*.sh" -type f -exec dos2unix {} \; && \
    chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]
