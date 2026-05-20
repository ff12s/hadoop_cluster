# syntax=docker/dockerfile:1.6

# =============================================================================
# Stage 1: downloader — скачивает и стрипает Hive, Tez, Tez UI, PostgreSQL JDBC.
# Результаты складываются в фиксированные пути /hive-dist, /tez-dist, /tez-ui-dist.
# =============================================================================
FROM hadoop-cluster-base:latest AS downloader

ARG HIVE_VERSION
ARG TEZ_VERSION

USER root

# Hive: стрип examples/docs + дублирующих hadoop-*.jar (в кластере используется tez.use.cluster.hadoop-libs=true)
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o hive.tar.gz \
        https://archive.apache.org/dist/hive/hive-${HIVE_VERSION}/apache-hive-${HIVE_VERSION}-bin.tar.gz && \
    mkdir -p /hive-dist && \
    tar -xzf /tmp/hive.tar.gz -C /hive-dist --strip-components=1 && \
    rm -f /tmp/hive.tar.gz && \
    rm -rf /hive-dist/examples /hive-dist/docs && \
    mkdir -p /hive-dist/warehouse /hive-dist/tmp /hive-dist/logs

# Tez: распаковка + удаление конфликтных JAR
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o tez.tar.gz \
        https://archive.apache.org/dist/tez/${TEZ_VERSION}/apache-tez-${TEZ_VERSION}-bin.tar.gz && \
    mkdir -p /tez-dist/conf && \
    tar -xzf /tmp/tez.tar.gz -C /tez-dist --strip-components=1 && \
    rm -f /tmp/tez.tar.gz && \
    rm -f /tez-dist/lib/hadoop-hdfs-client-*.jar \
          /tez-dist/lib/hadoop-mapreduce-client-common-*.jar \
          /tez-dist/lib/hadoop-mapreduce-client-core-*.jar \
          /tez-dist/lib/hadoop-yarn-server-timeline-pluginstorage-*.jar \
          /tez-dist/lib/protobuf-java-2.5.0.jar

# Tez UI: WAR из Maven Central, распакованный
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o tez-ui.war \
        https://repo1.maven.org/maven2/org/apache/tez/tez-ui/${TEZ_VERSION}/tez-ui-${TEZ_VERSION}.war && \
    mkdir -p /tez-ui-dist && \
    cd /tez-ui-dist && \
    jar xf /tmp/tez-ui.war && \
    rm -f /tmp/tez-ui.war

# PostgreSQL JDBC драйвер кладём прямо в Hive lib
RUN aria2c -x 16 -s 16 --file-allocation=none -d /hive-dist/lib -o postgresql-42.2.23.jar \
        https://jdbc.postgresql.org/download/postgresql-42.2.23.jar

RUN chown -R hadoop:hadoop /hive-dist /tez-dist /tez-ui-dist

# =============================================================================
# Stage 2: final
# =============================================================================
FROM hadoop-cluster-base:latest

ARG HIVE_VERSION
ARG TEZ_VERSION

ENV HIVE_VERSION=${HIVE_VERSION}
ENV TEZ_VERSION=${TEZ_VERSION}
ENV HIVE_HOME=/opt/hive
ENV TEZ_HOME=/opt/tez
ENV TEZ_CONF_DIR=$TEZ_HOME/conf
ENV PATH=$PATH:$HIVE_HOME/bin:$HADOOP_HOME/sbin
ENV HADOOP_CLASSPATH=$HADOOP_CONF_DIR:$HADOOP_HOME/share/hadoop/common/*:$HADOOP_HOME/share/hadoop/common/lib/*:$HADOOP_HOME/share/hadoop/hdfs/*:$HADOOP_HOME/share/hadoop/hdfs/lib/*:$TEZ_HOME/*:$TEZ_HOME/lib/*:$HIVE_HOME/lib/*

USER root

# PostgreSQL клиент — нужен скриптам метастора
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Один чистый слой с готовыми Hive/Tez/Tez-UI (без тарболов и build-артефактов)
COPY --from=downloader --chown=hadoop:hadoop /hive-dist /opt/hive
COPY --from=downloader --chown=hadoop:hadoop /tez-dist /opt/tez
COPY --from=downloader --chown=hadoop:hadoop /tez-ui-dist /opt/tez-ui

# Конфиги Hive
COPY config/ $HIVE_HOME/conf/
RUN cp $HIVE_HOME/conf/tez-site.xml $TEZ_CONF_DIR/tez-site.xml && \
    find $HIVE_HOME/conf -name "*.xml" -o -name "*.sh" -o -name "*.properties" | xargs dos2unix && \
    find $TEZ_CONF_DIR -name "*.xml" | xargs dos2unix

# Скрипты
COPY scripts/ /opt/scripts/
RUN find /opt/scripts -name "*.sh" -type f -exec dos2unix {} \; && \
    chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]
