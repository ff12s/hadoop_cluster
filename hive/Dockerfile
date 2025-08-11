FROM hadoop-cluster-base:latest

# Установка переменных окружения
ARG HIVE_VERSION=3.1.3
ENV HIVE_VERSION=${HIVE_VERSION}
ENV HIVE_HOME=/opt/hive
ENV PATH=$PATH:$HIVE_HOME/bin:$HADOOP_HOME/sbin
ENV HADOOP_CLASSPATH=$HADOOP_CONF_DIR:$HADOOP_CLASSPATH:$HIVE_HOME/lib/*

# Установка PostgreSQL клиента
USER root
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

# Скачивание и установка Hive
RUN aria2c -x 16 -s 16 -d /tmp -o apache-hive-${HIVE_VERSION}-bin.tar.gz \
    https://archive.apache.org/dist/hive/hive-${HIVE_VERSION}/apache-hive-${HIVE_VERSION}-bin.tar.gz && \
    tar -xzf /tmp/apache-hive-${HIVE_VERSION}-bin.tar.gz -C /opt && \
    mkdir -p /opt/hive && \
    rsync -a /opt/apache-hive-${HIVE_VERSION}-bin/ /opt/hive/ && \
    rm -rf /opt/apache-hive-${HIVE_VERSION}-bin && \
    rm -f /tmp/apache-hive-${HIVE_VERSION}-bin.tar.gz

# Скачивание PostgreSQL JDBC драйвера
RUN aria2c -x 16 -s 16 -d /tmp -o postgresql-42.2.23.jar \
    https://jdbc.postgresql.org/download/postgresql-42.2.23.jar && \
    mv /tmp/postgresql-42.2.23.jar $HIVE_HOME/lib/ && \
    chown hadoop:hadoop $HIVE_HOME/lib/postgresql-42.2.23.jar

# Создание директорий для Hive
RUN mkdir -p /opt/hive/warehouse && \
    mkdir -p /opt/hive/tmp && \
    mkdir -p /opt/hive/logs && \
    chown -R hadoop:hadoop /opt/hive

# Копирование конфигурационных файлов Hive
COPY config/ $HIVE_HOME/conf/

# Копирование скриптов
COPY scripts/ /opt/scripts/
RUN chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

# Проверка установки Hive
RUN ls -la $HIVE_HOME/bin && \
    ls -la $HIVE_HOME/lib | head -5

CMD ["/bin/bash"]
