FROM ubuntu:20.04

# Установка переменных окружения
ARG HADOOP_VERSION=3.3.6
ARG PYTHON_VERSION=3.8
ARG SCALA_VERSION=2.13.8

ENV HADOOP_VERSION=${HADOOP_VERSION}
ENV PYTHON_VERSION=${PYTHON_VERSION}
ENV SCALA_VERSION=${SCALA_VERSION}

# Установка Java 8
RUN apt-get update && apt-get install -y \
    openjdk-8-jdk \
    wget \
    curl \
    vim \
    net-tools \
    iputils-ping \
    telnet \
    openssh-server \
    openssh-client \
    rsync \
    aria2 \
    netcat \
    && rm -rf /var/lib/apt/lists/*

# Установка Python 3.8
RUN apt-get update && apt-get install -y \
    python3.8 \
    python3.8-dev \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Установка Scala
RUN aria2c -x 16 -s 16 -d /tmp -o scala-${SCALA_VERSION}.tgz \
    https://downloads.lightbend.com/scala/${SCALA_VERSION}/scala-${SCALA_VERSION}.tgz && \
    tar -xzf /tmp/scala-${SCALA_VERSION}.tgz -C /opt && \
    ln -s /opt/scala-${SCALA_VERSION} /opt/scala && \
    rm -f /tmp/scala-${SCALA_VERSION}.tgz

# Установка Hadoop
RUN aria2c -x 16 -s 16 -d /tmp -o hadoop-${HADOOP_VERSION}.tar.gz \
    https://archive.apache.org/dist/hadoop/common/hadoop-${HADOOP_VERSION}/hadoop-${HADOOP_VERSION}.tar.gz && \
    tar -xzf /tmp/hadoop-${HADOOP_VERSION}.tar.gz -C /opt && \
    mkdir -p /opt/hadoop && \
    rsync -a /opt/hadoop-${HADOOP_VERSION}/ /opt/hadoop/ && \
    rm -rf /opt/hadoop-${HADOOP_VERSION} && \
    rm -f /tmp/hadoop-${HADOOP_VERSION}.tar.gz

# Создание пользователя hadoop
RUN useradd -m -s /bin/bash hadoop && \
    echo "hadoop:hadoop" | chpasswd

# Настройка SSH
RUN mkdir -p /home/hadoop/.ssh && \
    ssh-keygen -t rsa -P '' -f /home/hadoop/.ssh/id_rsa && \
    cat /home/hadoop/.ssh/id_rsa.pub >> /home/hadoop/.ssh/authorized_keys && \
    chmod 0600 /home/hadoop/.ssh/authorized_keys && \
    chown -R hadoop:hadoop /home/hadoop/.ssh

# Создание директорий
RUN mkdir -p /opt/hadoop/logs && \
    mkdir -p /opt/hadoop/tmp && \
    mkdir -p /opt/hadoop/dfs/name && \
    mkdir -p /opt/hadoop/dfs/data && \
    mkdir -p /opt/scripts && \
    chown -R hadoop:hadoop /opt

# Установка переменных окружения
ENV JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64
ENV HADOOP_HOME=/opt/hadoop
ENV HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop
ENV HADOOP_LOG_DIR=/opt/hadoop/logs
ENV HADOOP_PID_DIR=/opt/hadoop/tmp
ENV SCALA_HOME=/opt/scala
ENV PATH=$PATH:$JAVA_HOME/bin:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$SCALA_HOME/bin

# Копирование конфигурации Hadoop
COPY config/ $HADOOP_HOME/etc/hadoop/

# Копирование скриптов
COPY scripts/ /opt/scripts/
RUN chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]
