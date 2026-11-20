-- Model latency per call and turn latency per diner turn, p50 and p95, per
-- location per UTC event day. A turn is the model plus tool time of the
-- events from a user_turn up to the next user_turn or action of that
-- conversation (metrics._turn_latencies); turns of 0 ms are left out.
-- approx_percentile is approximate, so it can differ slightly from the
-- nearest-rank figures in agent_daily_metrics.
CREATE OR REPLACE VIEW latency_percentiles AS
WITH marked AS (
    SELECT
        dt,
        company_id,
        location_id,
        conversation_id,
        seq,
        type,
        llm_latency_ms,
        tools_latency_ms,
        sum(IF(type IN ('user_turn', 'action'), 1, 0)) OVER (
            PARTITION BY dt, conversation_id ORDER BY seq
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS turn_no
    FROM events
),
turns AS (
    SELECT
        dt,
        company_id,
        location_id,
        sum(coalesce(llm_latency_ms, 0) + coalesce(tools_latency_ms, 0)) AS turn_ms
    FROM marked
    WHERE turn_no > 0
    GROUP BY dt, company_id, location_id, conversation_id, turn_no
    HAVING min_by(type, seq) = 'user_turn'
),
llm AS (
    SELECT
        dt,
        company_id,
        location_id,
        count(llm_latency_ms) AS llm_calls,
        approx_percentile(llm_latency_ms, 0.5) AS llm_p50_ms,
        approx_percentile(llm_latency_ms, 0.95) AS llm_p95_ms
    FROM events
    WHERE llm_latency_ms IS NOT NULL
    GROUP BY dt, company_id, location_id
),
turn AS (
    SELECT
        dt,
        company_id,
        location_id,
        count(*) AS turns,
        approx_percentile(turn_ms, 0.5) AS turn_p50_ms,
        approx_percentile(turn_ms, 0.95) AS turn_p95_ms
    FROM turns
    WHERE turn_ms > 0
    GROUP BY dt, company_id, location_id
)
SELECT
    coalesce(l.dt, t.dt) AS dt,
    coalesce(l.company_id, t.company_id) AS company_id,
    coalesce(l.location_id, t.location_id) AS location_id,
    l.llm_calls,
    l.llm_p50_ms,
    l.llm_p95_ms,
    t.turns,
    t.turn_p50_ms,
    t.turn_p95_ms
FROM llm l
FULL OUTER JOIN turn t
    ON t.dt = l.dt AND t.location_id = l.location_id
