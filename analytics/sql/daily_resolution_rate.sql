-- Share of the day's conversations the agent resolved itself (booked,
-- modified or cancelled), per location per UTC start day. Same definition
-- as agent_daily_metrics: resolved / conversations.
CREATE OR REPLACE VIEW daily_resolution_rate AS
SELECT
    dt,
    company_id,
    location_id,
    count(*) AS conversations,
    count_if(outcome IN ('booked', 'modified', 'cancelled')) AS resolved,
    CAST(count_if(outcome IN ('booked', 'modified', 'cancelled')) AS double)
        / count(*) AS resolution_rate
FROM conversations
GROUP BY dt, company_id, location_id
