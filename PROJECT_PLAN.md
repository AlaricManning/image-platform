# image-platform — Project Plan

A data platform on AWS for ingesting, processing, and serving data products built from
images and video clips. Starts simple (COCO batch ingestion) but the architecture is
shaped for the bigger picture: multiple third-party data providers, event-driven
processing, and queryable/curated data products for end users.

## Big picture

```
 Third parties                    Platform (our AWS account)
┌──────────────┐
│ partner-a    │  S3 event   ┌────────────┐    ┌─────────────────────────────┐
│ landing bkt  ├────────────▶│ EventBridge│───▶│ Router: Lambda (light work) │
└──────────────┘             │   rules    │    │         AWS Batch (heavy)   │
┌──────────────┐             └────────────┘    └──────────┬──────────────────┘
│ partner-b    │                                          │
│ landing bkt  │                                          ▼
└──────────────┘              BRONZE ──────▶ SILVER ──────▶ GOLD
                              raw,          validated,     curated data
                              immutable     normalized     products
                                                          │
                                              Glue Catalog + Athena
                                              (later: APIs, dashboards)
```

**Medallion layers**

| Layer | Bucket (one each) | Contents | Format |
|-------|-------------------|----------|--------|
| Landing | `…-landing-<partner>` | Whatever the partner drops (zips, loose files) | as-delivered |
| Bronze | `…-bronze` | Exact copies of raw data, unpacked, immutable, standardized keys | original formats |
| Silver | `…-silver` | Validated + enriched: image metadata, checksums, thumbnails, normalized annotations | Parquet + derived assets |
| Gold | `…-gold` | Curated products: per-category datasets, stats tables, ML-ready manifests | Parquet / product-specific |

**Key conventions (decide once, live with forever)**

- S3 keying: `partner=<id>/dataset=<name>/ingest_date=<YYYY-MM-DD>/...` — Hive-style
  partitions so Glue/Athena work for free later.
- Bronze is append-only/immutable; reprocessing always re-derives silver/gold from bronze.
- All processing is idempotent — same input object produces same output keys, safe to re-run.
- Every ingestion batch gets a manifest (what arrived, counts, checksums) so we can audit
  and re-drive processing without relying on replaying S3 events.

**Compute routing rule of thumb**

- Lambda: per-object, < 1 min, low memory (validation, manifest writes, routing, small metadata extraction).
- AWS Batch (Fargate): bulk / long-running (unpacking large archives, thumbnailing 5k images,
  annotation normalization, video transcode later).
- Step Functions: added later once there are multi-step flows worth orchestrating.

## Starting dataset: COCO

Use **COCO val2017** to start — 5,000 images (~1 GB) plus `annotations_trainval2017.zip`
(~241 MB, instances/keypoints/captions JSON). Big enough to make batch processing real,
small enough to keep S3/compute costs near zero. train2017 (~18 GB) can come later as a
"scale test." We simulate a third party by uploading the COCO zips into a partner landing
bucket — which conveniently gives us a realistic "partner drops an archive" ingestion story.

## Tech stack

- **IaC:** Terraform (modules + `envs/dev` layout, like terraform-practice), remote state via bootstrap.
- **Language:** Python 3.12 — boto3, Pillow, pyarrow/pandas; `pyproject.toml` + uv.
- **AWS:** S3, EventBridge, Lambda, AWS Batch on Fargate, Glue Data Catalog, Athena, DynamoDB (ingestion ledger), CloudWatch.
- **Region:** single region, dev-only environment to start.

## Phases

### Phase 0 — Scaffolding ✅ (2026-08-16)
- Repo layout: `infra/` (terraform: `bootstrap/`, `modules/`, `envs/dev/`), `src/image_platform/`
  (Python package), `jobs/` (Batch job containers), `lambdas/`, `scripts/`, `tests/`.
- Terraform bootstrap: state bucket + lockfile-based locking; dev env skeleton; tagging standard.
- Python project: pyproject, lint/format (ruff), pytest wiring.

### Phase 1 — Buckets + simulated partner ingestion ✅ (2026-08-16)
- Terraform: landing bucket for `partner=coco`, bronze/silver/gold buckets, lifecycle
  policies, encryption, public-access-block everywhere.
- `scripts/seed_coco.py`: download COCO val2017 + annotations, upload to the landing
  bucket as-is (this is us role-playing the third party).
- Manual "unpack to bronze" run of the Phase 2 logic before it's automated, to validate conventions.

### Phase 2 — Event-driven bronze ingestion ✅ (2026-08-16)
- S3 EventBridge notifications on landing buckets → EventBridge rule → ingestion Lambda.
- Lambda: validate the object (expected partner/type), write a record to a DynamoDB
  ingestion ledger, and either copy small objects straight to bronze or submit an AWS
  Batch job for archives.
- Batch job #1 (`unpack-archive`): stream-unzip landing archives into bronze under the
  standard key scheme; write the batch manifest.

### Phase 3 — Bronze → Silver batch processing ✅ (2026-08-16)
- Batch job #2 (`image-profile`): for each bronze image — dimensions, format, EXIF,
  checksum, generate thumbnail → thumbnails to silver, metadata rows to Parquet in silver.
- Batch job #3 (`normalize-annotations`): COCO instance/caption JSON → tidy Parquet tables
  (images, annotations, categories) partitioned by dataset.
- Triggered by bronze manifest completion events (EventBridge again), not per-object.

### Phase 4 — Gold + query layer ✅ (2026-08-16)
- Glue Data Catalog databases for silver/gold; Athena workgroup.
- First gold products: per-category image counts/stats table, an "ML-ready" manifest
  (image URI + label summary + thumbnail URI), maybe a small sampled dataset product.
- Athena smoke queries as the "end user" proof.

### Phase 5 — Iceberg lakehouse ✅ (2026-08-21, Spark local-mode on Fargate; EMR pending quota)
- Pivot from plain-parquet + partition projection to Apache Iceberg tables in the
  Glue Data Catalog (metastore only — no Glue ETL), written by PySpark on EMR
  Serverless. Partition values become real columns; the S3 path contract and its
  silent-empty failure mode go away.
- Batch extractors now write a silver `staging/` zone; `silver_build` (Spark)
  MERGEs/overwrite-partitions staging into Iceberg silver; `gold_build` (Spark)
  rebuilds gold from silver. Lambda keeps handling per-object events; Spark
  sweeps staging in batches.
- EMR Serverless vCPU quota increase denied 2026-08-21 (insufficient account usage
  history) — interim runtime is Spark local-mode in a Fargate container (same
  scripts, `imgp-dev-spark-runner` job definition). Re-request ~Sept 2026 after a
  billing cycle; then set `emr_enabled = true` and submit to EMR unchanged.

### Phase 6+ — The bigger picture (backlog, not now)
- Second partner/dataset to prove the multi-tenant landing pattern (e.g., Open Images subset).
- Iceberg maintenance automation (snapshot expiry, compaction) + orchestration of the Spark jobs (Step Functions or schedules).
- Clips/video: ffmpeg-based Batch jobs (frame extraction, scene detection) or MediaConvert.
- Step Functions orchestration + retries/DLQs; data-quality checks (Great Expectations or hand-rolled).
- Consumption APIs (API Gateway + Lambda over gold), dashboards, cross-account partner
  bucket access patterns (bucket policies / access points), cost monitoring + budgets.
- Scale test with train2017; Fargate Spot for batch fleets.

## Cost guardrails

Dev-scale COCO val2017 keeps this in pennies-per-month territory: ~1.5 GB of S3, Lambda
free tier, short Fargate runs. Guardrails from day one: lifecycle rules (abort incomplete
multipart uploads, expire landing objects after N days), Athena workgroup query byte
limit, an AWS Budget alarm, and never leaving Batch compute environments with min vCPUs > 0.
