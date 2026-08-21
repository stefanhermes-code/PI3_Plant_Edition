-- Applied 21 August 2026, before this runner existed. Recorded by backfill.
--
-- Charlie's production-quality load instruction: a quality test result belongs
-- to exactly one source, and a run-linked result's sample must belong to that
-- same run.
-- @count: physical_property_results, samples

alter table public.physical_property_results
  add constraint ck_ppr_single_source
  check (num_nonnulls(production_run_id, customer_trial_id, optimization_trial_id) = 1);

alter table public.samples
  add constraint uq_samples_id_production_run unique (id, production_run_id);

alter table public.physical_property_results
  add constraint fk_ppr_sample_belongs_to_run
  foreign key (sample_id, production_run_id)
  references public.samples (id, production_run_id);
