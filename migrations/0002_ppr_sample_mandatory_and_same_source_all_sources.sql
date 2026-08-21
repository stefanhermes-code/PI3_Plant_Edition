-- Applied 21 August 2026, before this runner existed. Recorded by backfill.
--
-- Charlie's follow-up: the same-source rule applies to every quality test
-- result, not only production-run results, and a sample is always required.
-- @count: physical_property_results, samples

alter table public.physical_property_results
  alter column sample_id set not null;

alter table public.samples
  add constraint ck_samples_single_source
  check (num_nonnulls(production_run_id, customer_trial_id, optimization_trial_id) = 1);

alter table public.samples
  add constraint uq_samples_id_customer_trial unique (id, customer_trial_id);

alter table public.samples
  add constraint uq_samples_id_optimization_trial unique (id, optimization_trial_id);

alter table public.physical_property_results
  add constraint fk_ppr_sample_belongs_to_customer_trial
  foreign key (sample_id, customer_trial_id)
  references public.samples (id, customer_trial_id);

alter table public.physical_property_results
  add constraint fk_ppr_sample_belongs_to_optimization_trial
  foreign key (sample_id, optimization_trial_id)
  references public.samples (id, optimization_trial_id);
