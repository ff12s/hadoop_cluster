FROM hadoop-cluster-base:latest

# Установка переменных окружения
ARG HIVE_VERSION
ARG TEZ_VERSION
ENV HIVE_VERSION=${HIVE_VERSION}
ENV TEZ_VERSION=${TEZ_VERSION}
ENV HIVE_HOME=/opt/hive
ENV TEZ_HOME=/opt/tez
ENV TEZ_CONF_DIR=$TEZ_HOME/conf
ENV PATH=$PATH:$HIVE_HOME/bin:$HADOOP_HOME/sbin
ENV HADOOP_CLASSPATH=$HADOOP_CONF_DIR:$HADOOP_HOME/share/hadoop/common/*:$HADOOP_HOME/share/hadoop/common/lib/*:$HADOOP_HOME/share/hadoop/hdfs/*:$HADOOP_HOME/share/hadoop/hdfs/lib/*:$TEZ_HOME/*:$TEZ_HOME/lib/*:$HIVE_HOME/lib/*

# Установка PostgreSQL клиента
USER root
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*

# Скачивание и установка Hive
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o apache-hive-${HIVE_VERSION}-bin.tar.gz \
    https://archive.apache.org/dist/hive/hive-${HIVE_VERSION}/apache-hive-${HIVE_VERSION}-bin.tar.gz && \
    tar -xzf /tmp/apache-hive-${HIVE_VERSION}-bin.tar.gz -C /opt && \
    mkdir -p /opt/hive && \
    rsync -a /opt/apache-hive-${HIVE_VERSION}-bin/ /opt/hive/ && \
    rm -rf /opt/apache-hive-${HIVE_VERSION}-bin && \
    rm -f /tmp/apache-hive-${HIVE_VERSION}-bin.tar.gz

# Скачивание PostgreSQL JDBC драйвера
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o postgresql-42.2.23.jar \
    https://jdbc.postgresql.org/download/postgresql-42.2.23.jar && \
    mv /tmp/postgresql-42.2.23.jar $HIVE_HOME/lib/ && \
    chown hadoop:hadoop $HIVE_HOME/lib/postgresql-42.2.23.jar

# Скачивание и установка Apache TEZ
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o apache-tez-${TEZ_VERSION}-bin.tar.gz \
    https://archive.apache.org/dist/tez/${TEZ_VERSION}/apache-tez-${TEZ_VERSION}-bin.tar.gz && \
    mkdir -p /opt/tez && \
    tar -xzf /tmp/apache-tez-${TEZ_VERSION}-bin.tar.gz -C /opt/tez --strip-components=1 && \
    mkdir -p /opt/tez/conf && \
    rm -f /tmp/apache-tez-${TEZ_VERSION}-bin.tar.gz

# Скачивание TEZ UI WAR (не входит в бинарный дистрибутив, берём из Maven Central)
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o tez-ui.war \
    https://repo1.maven.org/maven2/org/apache/tez/tez-ui/${TEZ_VERSION}/tez-ui-${TEZ_VERSION}.war && \
    mkdir -p /opt/tez-ui && \
    cd /opt/tez-ui && \
    jar xf /tmp/tez-ui.war && \
    rm -f /tmp/tez-ui.war && \
    chown -R hadoop:hadoop /opt/tez-ui

# Удаление конфликтных JAR из TEZ (используются библиотеки Hadoop 3.3.6 с кластера, tez.use.cluster.hadoop-libs=true)
RUN rm -f $TEZ_HOME/lib/hadoop-hdfs-client-*.jar && \
    rm -f $TEZ_HOME/lib/hadoop-mapreduce-client-common-*.jar && \
    rm -f $TEZ_HOME/lib/hadoop-mapreduce-client-core-*.jar && \
    rm -f $TEZ_HOME/lib/hadoop-yarn-server-timeline-pluginstorage-*.jar && \
    rm -f $TEZ_HOME/lib/protobuf-java-2.5.0.jar

# Создание директорий для Hive
RUN mkdir -p /opt/hive/warehouse && \
    mkdir -p /opt/hive/tmp && \
    mkdir -p /opt/hive/logs && \
    chown -R hadoop:hadoop /opt/hive && \
    chown -R hadoop:hadoop /opt/tez

# Копирование конфигурационных файлов Hive
COPY config/ $HIVE_HOME/conf/
# Копирование tez-site.xml в конфигурацию TEZ
RUN cp $HIVE_HOME/conf/tez-site.xml $TEZ_CONF_DIR/tez-site.xml
# Исправление окончаний строк в конфигурационных файлах
RUN find $HIVE_HOME/conf -name "*.xml" -o -name "*.sh" -o -name "*.properties" | xargs dos2unix && \
    find $TEZ_CONF_DIR -name "*.xml" | xargs dos2unix

# Копирование скриптов
COPY scripts/ /opt/scripts/
# Исправление окончаний строк в скриптах
RUN find /opt/scripts -name "*.sh" -type f -exec dos2unix {} \; && \
    chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

# Проверка установки Hive
RUN ls -la $HIVE_HOME/bin && \
    ls -la $HIVE_HOME/lib | head -5

CMD ["/bin/bash"]
