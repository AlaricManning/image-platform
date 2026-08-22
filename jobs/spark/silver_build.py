"""silver_build PySpark job (EMR Serverless): merge the silver staging zone
into Iceberg tables registered in the Glue Data Catalog.

- image_metadata is MERGEd on bronze_key (re-profiled images update in place)
- coco_* tables use overwritePartitions (a re-normalized drop replaces exactly
  the partitions it produced)

Tables are created on first run, partitioned on real columns — Iceberg tracks
files in table metadata, so the staging S3 layout is not a schema contract.

Submit:
  spark-submit silver_build.py --silver-bucket <bucket> [--database imgp_dev_silver]
"""

import argparse

from pyspark.sql import SparkSession

CATALOG = "glue"

# table -> (partition columns, merge key or None for overwritePartitions)
TABLES = {
    "image_metadata": (["partner", "dataset", "ingest_date"], "bronze_key"),
    "coco_images": (["partner", "dataset", "ingest_date", "split"], None),
    "coco_categories": (["partner", "dataset", "ingest_date", "split"], None),
    "coco_annotations": (["partner", "dataset", "ingest_date", "split"], None),
    "coco_captions": (["partner", "dataset", "ingest_date", "split"], None),
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--silver-bucket", required=True)
    parser.add_argument("--database", default="imgp_dev_silver")
    args = parser.parse_args()

    spark = SparkSession.builder.appName("silver_build").getOrCreate()

    for table, (partition_cols, merge_key) in TABLES.items():
        staging_path = f"s3://{args.silver_bucket}/staging/{table}/"
        try:
            df = spark.read.parquet(staging_path)
        except Exception as exc:
            print(f"skip {table}: no staging data ({exc})")
            continue

        target = f"{CATALOG}.{args.database}.{table}"
        if not spark.catalog.tableExists(target):
            print(f"creating {target} from {df.count()} staged rows")
            df.writeTo(target).partitionedBy(*partition_cols).create()
        elif merge_key:
            print(f"merging {df.count()} staged rows into {target} on {merge_key}")
            df.createOrReplaceTempView("staged")
            spark.sql(
                f"""
                MERGE INTO {target} t
                USING staged s
                ON t.{merge_key} = s.{merge_key}
                WHEN MATCHED THEN UPDATE SET *
                WHEN NOT MATCHED THEN INSERT *
                """
            )
        else:
            print(f"overwriting staged partitions of {target} ({df.count()} rows)")
            df.writeTo(target).overwritePartitions()

        snapshots = spark.sql(f"SELECT count(*) AS n FROM {target}.snapshots").collect()[0].n
        total = spark.table(target).count()
        print(f"{target}: {total} rows, {snapshots} snapshots")

    spark.stop()


if __name__ == "__main__":
    main()
