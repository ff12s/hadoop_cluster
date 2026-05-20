# syntax=docker/dockerfile:1.6

FROM hadoop-cluster-spark:latest

ARG SPARK_VERSION
ARG JUPYTER_VERSION

ENV SPARK_VERSION=${SPARK_VERSION}
ENV JUPYTER_VERSION=${JUPYTER_VERSION}
ENV SPARK_HOME=/opt/spark
ENV PATH=$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin

USER root

# JupyterLab + Toree (no pip cache) — единственная установка через apt отсутствует
RUN pip3 install --no-cache-dir --upgrade pip && \
    bash -lc 'if [ "$JUPYTER_VERSION" = "latest" ]; then pip3 install --no-cache-dir jupyterlab findspark toree; else pip3 install --no-cache-dir jupyterlab=="$JUPYTER_VERSION" findspark toree; fi' && \
    jupyter toree install --spark_home=/opt/spark --kernel_name="Toree - Scala" --interpreters=Scala

# Только новые директории — НЕ делаем chown -R /opt/spark*, чтобы не дублировать Spark в новый слой.
# /opt/spark/work и /opt/spark/logs уже созданы spark-образом.
RUN mkdir -p /notebooks /mnt/data /mnt/logs && \
    chown -R hadoop:hadoop /notebooks /mnt

# Скрипт запуска
COPY scripts/ /opt/scripts/
RUN find /opt/scripts -name "*.sh" -type f -exec dos2unix {} \; && \
    chmod +x /opt/scripts/* && chown -R hadoop:hadoop /opt/scripts

USER hadoop
WORKDIR /home/hadoop

CMD ["/bin/bash"]
