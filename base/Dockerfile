FROM ubuntu:20.04

# Установка переменных окружения
ARG HADOOP_VERSION
ARG PYTHON_VERSION
ARG SCALA_VERSION

ENV HADOOP_VERSION=${HADOOP_VERSION}
ENV PYTHON_VERSION=${PYTHON_VERSION}
ENV SCALA_VERSION=${SCALA_VERSION}

# Устанавливаем часовой пояс для автоматической установки
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Установка Java 8 и зависимостей для сборки Python
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
    build-essential \
    zlib1g-dev \
    libncurses5-dev \
    libgdbm-dev \
    libnss3-dev \
    libssl-dev \
    libreadline-dev \
    libffi-dev \
    libsqlite3-dev \
    libbz2-dev \
    tzdata \
    dos2unix \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Установка Python из исходного кода
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o Python-${PYTHON_VERSION}.tgz \
    https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz && \
    tar -xzf /tmp/Python-${PYTHON_VERSION}.tgz -C /tmp && \
    cd /tmp/Python-${PYTHON_VERSION} && \
    ./configure --prefix=/opt/python${PYTHON_VERSION} && \
    make -j$(nproc) && \
    make install && \
    ln -s /opt/python${PYTHON_VERSION}/bin/python${PYTHON_VERSION} /usr/local/bin/python${PYTHON_VERSION} && \
    ln -s /opt/python${PYTHON_VERSION}/bin/pip${PYTHON_VERSION} /usr/local/bin/pip${PYTHON_VERSION} && \
    ln -s /opt/python${PYTHON_VERSION}/bin/python${PYTHON_VERSION} /usr/local/bin/python3 && \
    ln -s /opt/python${PYTHON_VERSION}/bin/pip${PYTHON_VERSION} /usr/local/bin/pip3 && \
    ln -s /opt/python${PYTHON_VERSION} /opt/python && \
    cd / && \
    rm -rf /tmp/Python-${PYTHON_VERSION} && \
    rm -f /tmp/Python-${PYTHON_VERSION}.tgz

# Установка Scala
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o scala-${SCALA_VERSION}.tgz \
    https://downloads.lightbend.com/scala/${SCALA_VERSION}/scala-${SCALA_VERSION}.tgz && \
    tar -xzf /tmp/scala-${SCALA_VERSION}.tgz -C /opt && \
    ln -s /opt/scala-${SCALA_VERSION} /opt/scala && \
    rm -f /tmp/scala-${SCALA_VERSION}.tgz

# Установка Hadoop
RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o hadoop-${HADOOP_VERSION}.tar.gz \
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
ENV PYTHON_HOME=/opt/python${PYTHON_VERSION}
ENV PATH=$JAVA_HOME/bin:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$SCALA_HOME/bin:$PYTHON_HOME/bin:$PATH

# Копирование конфигурации Hadoop
COPY config/ $HADOOP_HOME/etc/hadoop/
# Исправление окончаний строк в конфигурационных файлах
RUN find $HADOOP_HOME/etc/hadoop -name "*.xml" -o -name "*.sh" -o -name "*.properties" | xargs dos2unix

# Копирование скриптов
COPY scripts/ /opt/scripts/
# Исправление окончаний строк в скриптах
RUN find /opt/scripts -name "*.sh" -type f -exec dos2unix {} \; && \
    chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]
