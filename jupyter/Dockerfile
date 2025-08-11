FROM hadoop-cluster-base:latest

ARG SPARK_VERSION=3.5.2
ARG JUPYTER_VERSION=latest

ENV SPARK_VERSION=${SPARK_VERSION}
ENV JUPYTER_VERSION=${JUPYTER_VERSION}
ENV SPARK_HOME=/opt/spark
ENV PATH=$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin

USER root

# Install Spark (Hadoop 3, Scala 2.13) and JupyterLab
RUN aria2c -x 16 -s 16 -d /tmp -o spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz \
    https://archive.apache.org/dist/spark/spark-${SPARK_VERSION}/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz && \
    tar -xzf /tmp/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz -C /opt && \
    ln -s /opt/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13 /opt/spark && \
    rm -f /tmp/spark-${SPARK_VERSION}-bin-hadoop3-scala2.13.tgz && \
    python3 -m pip install --no-cache-dir --upgrade pip && \
    bash -lc 'if [ "$JUPYTER_VERSION" = "latest" ]; then pip install --no-cache-dir jupyterlab findspark toree; else pip install --no-cache-dir jupyterlab=="$JUPYTER_VERSION" findspark toree; fi' && \
    jupyter toree install --spark_home=/opt/spark --kernel_name="Toree - Scala" --interpreters=Scala

# Prepare directories
RUN mkdir -p /notebooks /mnt/data /mnt/logs /opt/spark/work /opt/spark/logs && \
    chown -R hadoop:hadoop /notebooks /mnt /opt/spark*

# Copy start script
COPY scripts/ /opt/scripts/
RUN chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]


