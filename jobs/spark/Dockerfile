# Local-mode Spark runner: full Spark engine + Iceberg + Glue catalog in one
# container, for AWS Batch/Fargate. Interim runtime while the account's EMR
# Serverless vCPU quota is 0 — the PySpark scripts are runtime-agnostic and
# will move to EMR unchanged once quota is granted.
# bookworm pin: Spark 3.5 wants Java 17, which trixie no longer packages
FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pyspark==3.5.1

ENV SPARK_HOME=/usr/local/lib/python3.11/site-packages/pyspark

# Iceberg runtime + AWS clients (Glue/S3) + s3a for reading staging parquet.
RUN cd $SPARK_HOME/jars && curl -sfO \
    https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/1.6.1/iceberg-spark-runtime-3.5_2.12-1.6.1.jar \
    && curl -sfO https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-aws-bundle/1.6.1/iceberg-aws-bundle-1.6.1.jar \
    && curl -sfO https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar \
    && curl -sfO https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar

COPY spark-defaults.conf $SPARK_HOME/conf/spark-defaults.conf
COPY silver_build.py gold_build.py /app/
WORKDIR /app

ENTRYPOINT ["spark-submit", "--master", "local[*]"]
CMD ["silver_build.py"]
