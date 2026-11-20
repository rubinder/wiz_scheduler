-- Booked conversations over conversations with booking intent (a find_slots
-- tool_call in any of their events, whatever day it landed), per location
-- per UTC start day. Same definition as agent_daily_metrics:
-- booked / booking_intent.
CREATE OR REPLACE VIEW booking_conversion AS
WITH intent AS (
    SELECT DISTINCT conversation_id
    FROM events
    WHERE type = 'tool_call'
      AND json_extract_scalar(payload, '$.name') = 'find_slots'
)
SELECT
    c.dt,
    c.company_id,
    c.location_id,
    count(i.conversation_id) AS booking_intent,
    count_if(c.outcome = 'booked') AS booked,
    CAST(count_if(c.outcome = 'booked') AS double)
        / nullif(count(i.conversation_id), 0) AS booking_conversion
FROM conversations c
LEFT JOIN intent i ON i.conversation_id = c.conversation_id
GROUP BY c.dt, c.company_id, c.location_id
