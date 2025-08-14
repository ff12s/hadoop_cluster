FROM hadoop-cluster-base:latest

# Версии подтягиваются из build-args (compose передаёт из env_file)
ARG SPARK_VERSION
ARG OPENLINEAGE_VERSION
ARG SCALA_VERSION

ENV SPARK_VERSION=${SPARK_VERSION}
ENV SPARK_HOME=/opt/spark
ENV PATH=$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin
ENV OPENLINEAGE_VERSION=${OPENLINEAGE_VERSION}



USER root

# Установка Spark (бинарник для Hadoop 3, Scala ${SCALA_SHORT_VERSION})
RUN SCALA_SHORT_VERSION=$(echo ${SCALA_VERSION} | cut -d. -f1,2) && \
    aria2c -x 16 -s 16 -d /tmp -o spark-${SPARK_VERSION}-bin-hadoop3-scala${SCALA_SHORT_VERSION}.tgz \
    https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop3-scala${SCALA_SHORT_VERSION}.tgz && \
    tar -xzf /tmp/spark-${SPARK_VERSION}-bin-hadoop3-scala${SCALA_SHORT_VERSION}.tgz -C /opt && \
    ln -s /opt/spark-${SPARK_VERSION}-bin-hadoop3-scala${SCALA_SHORT_VERSION} /opt/spark && \
    rm -f /tmp/spark-${SPARK_VERSION}-bin-hadoop3-scala${SCALA_SHORT_VERSION}.tgz && \
    mkdir -p /opt/spark/work /opt/spark/logs && \
    chown -R hadoop:hadoop /opt/spark*

# Добавление OpenLineage Spark Agent (Scala ${SCALA_SHORT_VERSION})
RUN SCALA_SHORT_VERSION=$(echo ${SCALA_VERSION} | cut -d. -f1,2) && \
    aria2c -x 16 -s 16 -d /opt/spark/jars -o openlineage-spark_${SCALA_SHORT_VERSION}-${OPENLINEAGE_VERSION}.jar \
    https://repo1.maven.org/maven2/io/openlineage/openlineage-spark_${SCALA_SHORT_VERSION}/${OPENLINEAGE_VERSION}/openlineage-spark_${SCALA_SHORT_VERSION}-${OPENLINEAGE_VERSION}.jar && \
    chown -R hadoop:hadoop /opt/spark/jars

# Конфигурация Spark
COPY config/ /opt/spark/conf/
# Исправление окончаний строк в конфигурационных файлах
RUN find /opt/spark/conf -name "*.xml" -o -name "*.sh" -o -name "*.properties" -o -name "*.conf" | xargs dos2unix

# Скрипты запуска
COPY scripts/ /opt/scripts/
# Исправление окончаний строк в скриптах
RUN find /opt/scripts -name "*.sh" -type f -exec dos2unix {} \; && \
    chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]


