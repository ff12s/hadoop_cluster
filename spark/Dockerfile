FROM hadoop-cluster-base:latest

# Версии подтягиваются из build-args (compose передаёт из env_file)
ARG SPARK_VERSION
ARG OPENLINEAGE_VERSION

ENV SPARK_VERSION=${SPARK_VERSION}
ENV SPARK_HOME=/opt/spark
ENV PATH=$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin
ENV OPENLINEAGE_VERSION=${OPENLINEAGE_VERSION}

USER root

# Установка Spark (бинарник для Hadoop 3, Scala 2.13)
RUN aria2c -x 16 -s 16 -d /tmp -o spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz \
    https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz && \
    tar -xzf /tmp/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz -C /opt && \
    ln -s /opt/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13 /opt/spark && \
    rm -f /tmp/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz && \
    mkdir -p /opt/spark/work /opt/spark/logs && \
    chown -R hadoop:hadoop /opt/spark*

# Добавление OpenLineage Spark Agent (Scala 2.13)
RUN aria2c -x 16 -s 16 -d /opt/spark/jars -o openlineage-spark_2.13-${OPENLINEAGE_VERSION}.jar \
    https://repo1.maven.org/maven2/io/openlineage/openlineage-spark_2.13/${OPENLINEAGE_VERSION}/openlineage-spark_2.13-${OPENLINEAGE_VERSION}.jar && \
    chown -R hadoop:hadoop /opt/spark/jars

# Конфигурация Spark
COPY config/ /opt/spark/conf/

# Скрипты запуска
COPY scripts/ /opt/scripts/
RUN chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]


