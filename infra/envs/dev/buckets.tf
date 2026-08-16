# Medallion buckets + per-partner landing buckets.
#
# Landing holds whatever the partner drops (as-delivered keys, expires after 30
# days — bronze is the durable copy). Bronze/silver/gold use the standard
# partner=/dataset=/ingest_date= key scheme. force_destroy everywhere because
# this is a torn-down-freely dev environment.

module "landing_coco" {
  source = "../../modules/data-bucket"

  name          = "${local.prefix}-dev-landing-coco-${local.account_id}"
  expire_days   = 30
  eventbridge   = true
  force_destroy = true
}

module "bronze" {
  source = "../../modules/data-bucket"

  name          = "${local.prefix}-dev-bronze-${local.account_id}"
  eventbridge   = true
  force_destroy = true
}

module "silver" {
  source = "../../modules/data-bucket"

  name          = "${local.prefix}-dev-silver-${local.account_id}"
  force_destroy = true
}

module "gold" {
  source = "../../modules/data-bucket"

  name          = "${local.prefix}-dev-gold-${local.account_id}"
  force_destroy = true
}

output "buckets" {
  value = {
    landing_coco = module.landing_coco.bucket
    bronze       = module.bronze.bucket
    silver       = module.silver.bucket
    gold         = module.gold.bucket
  }
}
