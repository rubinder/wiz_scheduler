-- Handoffs to staff by reason, per location per UTC event day. The lake
-- holds only known reason codes; a free-form reason lands as "other".
CREATE OR REPLACE VIEW handoff_reasons AS
SELECT
    dt,
    company_id,
    location_id,
    coalesce(json_extract_scalar(payload, '$.reason'), 'other') AS reason,
    count(*) AS handoffs
FROM events
WHERE type = 'handoff'
GROUP BY dt, company_id, location_id,
         coalesce(json_extract_scalar(payload, '$.reason'), 'other')
