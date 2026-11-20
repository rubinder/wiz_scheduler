-- Share of screened calls the screen blocked, per location per UTC day.
-- Same definition as agent_daily_metrics: blocked / screened.
CREATE OR REPLACE VIEW spam_blocked_share AS
SELECT
    dt,
    company_id,
    location_id,
    count(*) AS screened,
    count_if(decision = 'block') AS blocked,
    CAST(count_if(decision = 'block') AS double) / count(*) AS blocked_share
FROM screened_calls
GROUP BY dt, company_id, location_id
