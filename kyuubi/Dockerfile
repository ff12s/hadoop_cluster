FROM hadoop-cluster-spark:latest

ARG SPARK_VERSION
ARG KYUUBI_VERSION

ENV SPARK_VERSION=${SPARK_VERSION}
ENV KYUUBI_VERSION=${KYUUBI_VERSION}
ENV SPARK_HOME=/opt/spark
ENV KYUUBI_HOME=/opt/kyuubi
ENV PATH=$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin:$KYUUBI_HOME/bin

USER root

# Install Kyuubi
RUN aria2c -x 16 -s 16 -d /tmp -o apache-kyuubi-${KYUUBI_VERSION}-bin.tgz \
    https://archive.apache.org/dist/kyuubi/kyuubi-${KYUUBI_VERSION}/apache-kyuubi-${KYUUBI_VERSION}-bin.tgz && \
    tar -xzf /tmp/apache-kyuubi-${KYUUBI_VERSION}-bin.tgz -C /opt && \
    ln -s /opt/apache-kyuubi-${KYUUBI_VERSION}-bin /opt/kyuubi && \
    rm -f /tmp/apache-kyuubi-${KYUUBI_VERSION}-bin.tgz && \
    mkdir -p /opt/kyuubi/logs /opt/kyuubi/work /opt/kyuubi/pid && \
    chown -R hadoop:hadoop /opt/apache-kyuubi-${KYUUBI_VERSION}-bin /opt/kyuubi /opt/spark /opt/spark* && \
    chmod -R 775 /opt/apache-kyuubi-${KYUUBI_VERSION}-bin /opt/kyuubi

# Copy config and scripts
COPY config/ /opt/kyuubi/conf/
COPY scripts/ /opt/scripts/
RUN chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]
