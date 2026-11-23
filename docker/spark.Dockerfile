# Spark 3.4.0 + OpenJDK 11 + Python 3.8 (Ubuntu 20.04) - same versions as the WSL setup and the Mesos cluster
FROM apache/spark:3.4.0-scala2.12-java11-python3-ubuntu

USER root
RUN pip3 install --no-cache-dir pytest==8.3.5

ENV PATH=/opt/spark/bin:$PATH \
    PYSPARK_PYTHON=python3 \
    PYTHONPATH=/opt/spark/python:/opt/spark/python/lib/py4j-0.10.9.7-src.zip \
    PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/formula1
ENTRYPOINT []
