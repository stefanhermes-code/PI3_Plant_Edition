-- Charlie's closeout of 21 August 2026, action 1: the production-quality
-- replacement workstream is accepted and closed, the 4,392 replacement results
-- stand as the active UAT dataset, and the backup of the 1,454 superseded
-- run-level rows is no longer needed.
--
-- Applied through the Supabase SQL interface rather than this runner, because
-- the runner cannot reach the hosted database from the working session.
-- Recorded here so the ledger is a complete history of schema changes rather
-- than only of the ones that happened to be convenient to run through it.
-- @count: physical_property_results

drop table if exists public.physical_property_results_legacy_20260821;
