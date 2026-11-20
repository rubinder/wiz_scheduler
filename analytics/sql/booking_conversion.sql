-- Booked conversations over conversations with booking intent (a find_slots
-- tool_call in any of their events, whatever day it landed), per location
-- per UTC start day. Same definition as agent_daily_metrics:
-- booked / booking_intent.
--
-- A conversation's events never land before the day it started, so intent
-- is looked up only in events partitions at or after the conversation's
-- dt; with a dt filter on this view, Athena's dynamic filtering can skip
-- the earlier events partitions. A conversation with intent events on two
-- days joins twice, hence the DISTINCT counts.
CREATE OR REPLACE VIEW booking_conversion AS
WITH intent AS (
    SELECT DISTINCT dt, conversation_id
    FROM events
    WHERE type = 'tool_call'
      AND json_extract_scalar(payload, '$.name') = 'find_slots'
)
SELECT
    c.dt,
    c.company_id,
    c.location_id,
    count(DISTINCT i.conversation_id) AS booking_intent,
    count(DISTINCT IF(c.outcome = 'booked', c.conversation_id)) AS booked,
    CAST(count(DISTINCT IF(c.outcome = 'booked', c.conversation_id)) AS double)
        / nullif(count(DISTINCT i.conversation_id), 0) AS booking_conversion
FROM conversations c
LEFT JOIN intent i
    ON i.conversation_id = c.conversation_id
   AND i.dt >= c.dt
GROUP BY c.dt, c.company_id, c.location_id
