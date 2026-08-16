# image-platform

Data platform for ingesting / processing / providing data products related to images.
See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the architecture and phased roadmap.

## Layout

- `infra/bootstrap/` — one-time setup (remote-state bucket + `image-platform-deploy` IAM user); local state, run as admin `default` profile
- `infra/envs/dev/` — dev environment; remote state in `imgp-tfstate-<account>`, runs as the `image-platform` AWS profile
- `src/image_platform/` — shared Python library
- `lambdas/`, `jobs/`, `scripts/` — event handlers, Batch job containers, operational scripts (added per phase)

## Conventions

- All project IAM roles/policies are named `imgp-*` — the deploy user's IAM permissions are scoped to that prefix.
- S3 keys use Hive-style partitions: `partner=<id>/dataset=<name>/ingest_date=<YYYY-MM-DD>/...`
- Bronze is immutable; silver/gold are always re-derivable from bronze.

## Dev setup

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'

cd infra/envs/dev
terraform init
terraform plan
```
