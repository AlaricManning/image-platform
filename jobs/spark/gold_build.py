"""gold_build PySpark job (EMR Serverless): build gold Iceberg tables from
silver Iceberg tables for one partner/split.

  category_stats: per-category annotation/image counts, avg bbox area, crowd count
  ml_manifest:    per-image bronze/thumbnail URIs, dimensions, checksum, label summary

Gold is fully derived, so both tables are rebuilt with createOrReplace — a
deliberate contrast with silver's incremental MERGE/overwritePartitions.
"""

import argparse

from pyspark.sql import SparkSession

CATALOG = "glue"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--partner", required=True)
    parser.add_argument("--images-dataset", required=True)
    parser.add_argument("--annotations-dataset", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--ingest-date", required=True)
    parser.add_argument("--silver-database", default="imgp_dev_silver")
    parser.add_argument("--gold-database", default="imgp_dev_gold")
    args = parser.parse_args()

    spark = SparkSession.builder.appName("gold_build").getOrCreate()
    silver = f"{CATALOG}.{args.silver_database}"
    gold = f"{CATALOG}.{args.gold_database}"

    spark.sql(
        f"""
        SELECT * FROM {silver}.coco_annotations
        WHERE partner = '{args.partner}' AND dataset = '{args.annotations_dataset}'
          AND ingest_date = '{args.ingest_date}' AND split = '{args.split}'
        """
    ).createOrReplaceTempView("ann")
    spark.sql(
        f"""
        SELECT * FROM {silver}.coco_categories
        WHERE partner = '{args.partner}' AND dataset = '{args.annotations_dataset}'
          AND ingest_date = '{args.ingest_date}' AND split = '{args.split}'
        """
    ).createOrReplaceTempView("cat")
    spark.sql(
        f"""
        SELECT * FROM {silver}.coco_images
        WHERE partner = '{args.partner}' AND dataset = '{args.annotations_dataset}'
          AND ingest_date = '{args.ingest_date}' AND split = '{args.split}'
        """
    ).createOrReplaceTempView("img")
    spark.sql(
        f"""
        SELECT * FROM {silver}.image_metadata
        WHERE partner = '{args.partner}' AND dataset = '{args.images_dataset}'
          AND ingest_date = '{args.ingest_date}'
        """
    ).createOrReplaceTempView("meta")

    category_stats = spark.sql(
        f"""
        SELECT
            a.partner,
            '{args.images_dataset}' AS dataset,
            a.ingest_date,
            a.category_id,
            c.name AS category,
            c.supercategory,
            count(*) AS n_annotations,
            count(DISTINCT a.image_id) AS n_images,
            avg(a.area) AS avg_area,
            sum(a.iscrowd) AS crowd_annotations
        FROM ann a
        JOIN cat c ON a.category_id = c.category_id
        GROUP BY a.partner, a.ingest_date, a.category_id, c.name, c.supercategory
        """
    )
    category_stats.writeTo(f"{gold}.category_stats").partitionedBy(
        "partner", "dataset", "ingest_date"
    ).createOrReplace()
    print(f"category_stats: {spark.table(f'{gold}.category_stats').count()} rows")

    spark.sql(
        """
        SELECT
            a.image_id,
            count(*) AS n_annotations,
            count(DISTINCT c.name) AS n_categories,
            concat_ws(',', sort_array(collect_set(c.name))) AS categories
        FROM ann a
        JOIN cat c ON a.category_id = c.category_id
        GROUP BY a.image_id
        """
    ).createOrReplaceTempView("per_image")

    ml_manifest = spark.sql(
        """
        SELECT
            m.partner,
            m.dataset,
            m.ingest_date,
            i.image_id,
            i.file_name,
            i.width,
            i.height,
            m.bronze_key,
            m.thumbnail_key,
            m.size_bytes,
            m.sha256,
            coalesce(p.n_annotations, 0) AS n_annotations,
            coalesce(p.n_categories, 0) AS n_categories,
            coalesce(p.categories, '') AS categories
        FROM img i
        JOIN meta m ON i.file_name = m.file_name
        LEFT JOIN per_image p ON i.image_id = p.image_id
        """
    )
    ml_manifest.writeTo(f"{gold}.ml_manifest").partitionedBy(
        "partner", "dataset", "ingest_date"
    ).createOrReplace()
    print(f"ml_manifest: {spark.table(f'{gold}.ml_manifest').count()} rows")

    spark.stop()


if __name__ == "__main__":
    main()
