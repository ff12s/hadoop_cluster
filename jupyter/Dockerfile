FROM hadoop-cluster-spark:latest

ARG SPARK_VERSION
ARG JUPYTER_VERSION

ENV SPARK_VERSION=${SPARK_VERSION}
ENV JUPYTER_VERSION=${JUPYTER_VERSION}
ENV SPARK_HOME=/opt/spark
ENV PATH=$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin

USER root

# Install JupyterLab + Toree
RUN python3 -m pip install --no-cache-dir --upgrade pip && \
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


