-- Covers (party sizes) of live bookings of either provider, by the UTC date
-- and UTC hour they start, per location.
--
-- The lake holds neither a location's time zone nor its rules' service
-- periods, so this view buckets by UTC hour; the named-period figure
-- (dinner, lunch, ... in the location's local time) is
-- agent_daily_metrics.covers_by_period in Postgres. A reservation lands in
-- the partition of the day it was created, with its status as of that run,
-- so a later cancellation is not reflected here.
CREATE OR REPLACE VIEW covers_by_service_period AS
SELECT
    company_id,
    location_id,
    provider,
    CAST(start_at AS date) AS start_date_utc,
    hour(start_at) AS start_hour_utc,
    count(*) AS bookings,
    sum(party_size) AS covers
FROM reservations
WHERE status IN ('booked', 'seated', 'completed')
GROUP BY company_id, location_id, provider, CAST(start_at AS date), hour(start_at)
