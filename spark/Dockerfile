FROM hadoop-cluster-base:latest

# Используем версии из ARG с дефолтами (будут переопределены при сборке)
ARG SPARK_VERSION=3.5.2

ENV SPARK_VERSION=${SPARK_VERSION}
ENV SPARK_HOME=/opt/spark
ENV PATH=$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin

USER root

# Установка Spark (бинарник для Hadoop 3, Scala 2.13)
RUN aria2c -x 16 -s 16 -d /tmp -o spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz \
    https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz && \
    tar -xzf /tmp/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz -C /opt && \
    ln -s /opt/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13 /opt/spark && \
    rm -f /tmp/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz && \
    mkdir -p /opt/spark/work /opt/spark/logs && \
    chown -R hadoop:hadoop /opt/spark*

# Конфигурация Spark
COPY config/ /opt/spark/conf/

# Скрипты запуска
COPY scripts/ /opt/scripts/
RUN chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]


