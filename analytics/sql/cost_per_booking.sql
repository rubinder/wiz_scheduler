-- Model cost and billed amount per booked conversation, per location per
-- UTC start day. cost_usd is what the model calls cost us; billed_usd is
-- what the conversations were billed (NULL until billed, counted as 0).
CREATE OR REPLACE VIEW cost_per_booking AS
SELECT
    dt,
    company_id,
    location_id,
    count(*) AS conversations,
    count_if(outcome = 'booked') AS booked,
    sum(cost_usd) AS cost_usd,
    sum(coalesce(billed_usd, 0)) AS billed_usd,
    sum(cost_usd) / nullif(count_if(outcome = 'booked'), 0) AS cost_per_booking_usd,
    sum(coalesce(billed_usd, 0)) / nullif(count_if(outcome = 'booked'), 0)
        AS billed_per_booking_usd
FROM conversations
GROUP BY dt, company_id, location_id
