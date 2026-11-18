# Reservations + AI host: decisions log

Decisions made while building without stopping to ask (owner instruction,
2026-11-18). Each: what, why, cost if wrong. Newest last.

1. **Churn is part of a booking's table range.** `reservation_tables.during`
   = dining start to dining end + the party band's churn. The database's
   no-overlap constraint therefore enforces churn too. Cost if wrong: a
   churn change in the rules doesn't move existing bookings.
2. **Capacity and pacing count dining time only**, not churn: diners leave
   when they finish; the reset only blocks the table.
3. **Staff bookings ignore the booking cutoff** (e.g. "no bookings within
   60 minutes"), so a host can book a walk-in for 15 minutes from now. The
   AI agent keeps the cutoff. Booking horizon still applies to everyone.
4. **Merging to main with the feature switch off.** Each step merges after
   green CI with `RESERVATIONS_ENABLED=false` in production, so new tables
   ship but nothing is visible. Billable AWS resources (Connect numbers,
   Lex bots) default to zero in Terraform until the owner switches them on.
5. **Tenant isolation for foreign keys is enforced in the services, not with
   composite foreign keys.** A security scan noted that FK checks bypass
   row-level security, so a crafted id could point at another tenant's
   table. Every service resolves ids from the payload with
   `company_id` + `location_id` filters and rejects unknown ones; tests
   cover it. This matches how the rest of the schema is built (simple FKs).
   Cost if wrong: a missed check in a future service could link rows across
   tenants; composite FKs would be the stronger backstop to add later.
6. **All availability math runs in UTC instants**, converting to the
   location zone only to read wall-clock rules and to present results.
   Review caught that Python adds durations to zoned datetimes by wall
   clock, which made meals an hour short on the spring-forward night and
   could double-book. Cost if wrong: none known; DST tests pin it.
7. **A hold with no expiry time blocks its table** (fail closed), per a
   security review: a missing value must never free capacity.
8. **Staff can't book start times in the past**, even with the cutoff
   bypass; a walk-in already at the table is recorded by seating it.
9. **Section cover limits count dining time only**, like restaurant
   capacity (decision 2).
10. **A diner can't grow a booking past the self-booking party limit** by
    modifying it; that would skip staff approval. Staff can.
11. **Lock order is location first.** Every write locks the location row,
    then touches reservations. Future code must keep that order to avoid
    deadlocks.
