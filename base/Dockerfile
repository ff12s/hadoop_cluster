# syntax=docker/dockerfile:1.6

# =============================================================================
# Stage 1: python-builder — компиляция Python из исходников.
# Весь build-essential и *-dev остаются здесь и в финал не попадают.
# =============================================================================
FROM ubuntu:20.04 AS python-builder

ARG PYTHON_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        aria2 \
        ca-certificates \
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
    && rm -rf /var/lib/apt/lists/*

RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o Python-${PYTHON_VERSION}.tgz \
        https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz && \
    tar -xzf /tmp/Python-${PYTHON_VERSION}.tgz -C /tmp && \
    cd /tmp/Python-${PYTHON_VERSION} && \
    ./configure --prefix=/opt/python${PYTHON_VERSION} --enable-optimizations --with-ensurepip=install && \
    make -j"$(nproc)" && \
    make install && \
    cd / && \
    rm -rf /tmp/Python-${PYTHON_VERSION} /tmp/Python-${PYTHON_VERSION}.tgz

# =============================================================================
# Stage 2: downloader — скачивает и стрипает Scala/Hadoop.
# Тарболы и source jar'ы остаются здесь.
# =============================================================================
FROM ubuntu:20.04 AS downloader

ARG HADOOP_VERSION
ARG SCALA_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        aria2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o scala-${SCALA_VERSION}.tgz \
        https://downloads.lightbend.com/scala/${SCALA_VERSION}/scala-${SCALA_VERSION}.tgz && \
    tar -xzf /tmp/scala-${SCALA_VERSION}.tgz -C /opt && \
    rm -rf /opt/scala-${SCALA_VERSION}/doc /opt/scala-${SCALA_VERSION}/man && \
    rm -f /tmp/scala-${SCALA_VERSION}.tgz

RUN aria2c -x 16 -s 16 --file-allocation=none -d /tmp -o hadoop.tar.gz \
        https://archive.apache.org/dist/hadoop/common/hadoop-${HADOOP_VERSION}/hadoop-${HADOOP_VERSION}.tar.gz && \
    mkdir -p /opt/hadoop && \
    tar -xzf /tmp/hadoop.tar.gz -C /opt/hadoop --strip-components=1 && \
    rm -f /tmp/hadoop.tar.gz && \
    rm -rf /opt/hadoop/share/doc && \
    find /opt/hadoop -type f \( -name '*-sources.jar' -o -name '*-tests.jar' -o -name '*-test-sources.jar' \) -delete && \
    find /opt/hadoop/share -type d -name sources -prune -exec rm -rf {} + && \
    mkdir -p /opt/hadoop/logs /opt/hadoop/tmp /opt/hadoop/dfs/name /opt/hadoop/dfs/data

# =============================================================================
# Stage 3: final — рантайм-образ.
# =============================================================================
FROM ubuntu:20.04

ARG HADOOP_VERSION
ARG PYTHON_VERSION
ARG SCALA_VERSION

ENV HADOOP_VERSION=${HADOOP_VERSION}
ENV PYTHON_VERSION=${PYTHON_VERSION}
ENV SCALA_VERSION=${SCALA_VERSION}
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=UTC

# Runtime-пакеты + создание пользователя hadoop ДО COPY, чтобы --chown=hadoop работал.
# Debug-утилиты оставлены сознательно: vim, telnet, ping, netcat, net-tools, aria2, rsync.
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-8-jdk \
        ca-certificates \
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
        tzdata \
        dos2unix \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash hadoop \
    && echo "hadoop:hadoop" | chpasswd \
    && mkdir -p /home/hadoop/.ssh \
    && ssh-keygen -t rsa -P '' -f /home/hadoop/.ssh/id_rsa \
    && cat /home/hadoop/.ssh/id_rsa.pub >> /home/hadoop/.ssh/authorized_keys \
    && chmod 0600 /home/hadoop/.ssh/authorized_keys \
    && chown -R hadoop:hadoop /home/hadoop/.ssh \
    && mkdir -p /opt/scripts \
    && chown hadoop:hadoop /opt /opt/scripts

# Чистые слои с правильным владельцем (--chown избегает дублирующего chown -R).
COPY --from=python-builder --chown=hadoop:hadoop /opt/python${PYTHON_VERSION} /opt/python${PYTHON_VERSION}
COPY --from=downloader --chown=hadoop:hadoop /opt/scala-${SCALA_VERSION} /opt/scala-${SCALA_VERSION}
COPY --from=downloader --chown=hadoop:hadoop /opt/hadoop /opt/hadoop

# Симлинки для Python, Scala — выполняются под root, владелец симлинков не важен
RUN ln -s /opt/python${PYTHON_VERSION}/bin/python${PYTHON_VERSION} /usr/local/bin/python${PYTHON_VERSION} && \
    ln -s /opt/python${PYTHON_VERSION}/bin/pip${PYTHON_VERSION} /usr/local/bin/pip${PYTHON_VERSION} && \
    ln -s /opt/python${PYTHON_VERSION}/bin/python${PYTHON_VERSION} /usr/local/bin/python3 && \
    ln -s /opt/python${PYTHON_VERSION}/bin/pip${PYTHON_VERSION} /usr/local/bin/pip3 && \
    ln -s /opt/python${PYTHON_VERSION} /opt/python && \
    ln -s /opt/scala-${SCALA_VERSION} /opt/scala

ENV JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64
ENV HADOOP_HOME=/opt/hadoop
ENV HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop
ENV HADOOP_LOG_DIR=/opt/hadoop/logs
ENV HADOOP_PID_DIR=/opt/hadoop/tmp
ENV SCALA_HOME=/opt/scala
ENV PYTHON_HOME=/opt/python${PYTHON_VERSION}
ENV PATH=$JAVA_HOME/bin:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$SCALA_HOME/bin:$PYTHON_HOME/bin:$PATH

# Конфигурация Hadoop
COPY --chown=hadoop:hadoop config/ $HADOOP_HOME/etc/hadoop/
RUN find $HADOOP_HOME/etc/hadoop -name "*.xml" -o -name "*.sh" -o -name "*.properties" | xargs dos2unix

# Скрипты
COPY --chown=hadoop:hadoop scripts/ /opt/scripts/
RUN find /opt/scripts -name "*.sh" -type f -exec dos2unix {} \; && \
    chmod +x /opt/scripts/*

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]
