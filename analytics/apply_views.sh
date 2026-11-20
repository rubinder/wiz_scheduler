#!/usr/bin/env bash
# Create or replace the Athena views in analytics/sql (#259 step 10).
#
#   analytics/apply_views.sh [workgroup] [database]
#
# Defaults: workgroup wizscheduler-analytics, database wizscheduler_analytics
# (terraform/analytics.tf with app_name = "wizscheduler"). Needs the
# analytics_reader policy and AWS credentials for the account. Each view is
# one CREATE OR REPLACE VIEW, so rerunning is safe.
set -euo pipefail

WORKGROUP="${1:-wizscheduler-analytics}"
DATABASE="${2:-wizscheduler_analytics}"
SQL_DIR="$(cd "$(dirname "$0")" && pwd)/sql"

for file in "$SQL_DIR"/*.sql; do
  name="$(basename "$file" .sql)"
  id="$(aws athena start-query-execution \
    --work-group "$WORKGROUP" \
    --query-execution-context "Database=$DATABASE" \
    --query-string "file://$file" \
    --query QueryExecutionId --output text)"
  while true; do
    state="$(aws athena get-query-execution --query-execution-id "$id" \
      --query QueryExecution.Status.State --output text)"
    case "$state" in
      SUCCEEDED) echo "$name: ok"; break ;;
      FAILED|CANCELLED)
        reason="$(aws athena get-query-execution --query-execution-id "$id" \
          --query QueryExecution.Status.StateChangeReason --output text)"
        echo "$name: $state: $reason" >&2
        exit 1 ;;
      *) sleep 1 ;;
    esac
  done
done
