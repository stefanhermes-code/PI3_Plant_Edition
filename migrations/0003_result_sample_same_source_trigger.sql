-- Applied 21 August 2026, before this runner existed. Recorded by backfill.
--
-- The composite foreign keys above are the guarantee. This trigger exists so
-- the application shows a readable sentence rather than a constraint name.
-- @count: physical_property_results

create or replace function public.check_result_sample_matches_source()
returns trigger
language plpgsql
as $function$
declare
    s_run integer;
    s_ct  integer;
    s_ot  integer;
begin
    if new.sample_id is null then
        raise exception 'Quality test result must reference a sample.';
    end if;

    select production_run_id, customer_trial_id, optimization_trial_id
      into s_run, s_ct, s_ot
      from public.samples
     where id = new.sample_id;

    if not found then
        raise exception 'Quality test result names sample % which does not exist.', new.sample_id;
    end if;

    if num_nonnulls(new.production_run_id, new.customer_trial_id,
                    new.optimization_trial_id) <> 1 then
        raise exception
            'Quality test result must belong to exactly one of a production run, a customer trial or an optimization trial.';
    end if;

    if new.production_run_id is not null and s_run is distinct from new.production_run_id then
        raise exception 'Quality test result names production run % but its sample % belongs to %.',
            new.production_run_id, new.sample_id,
            coalesce('run ' || s_run::text, 'customer trial ' || s_ct::text,
                     'optimization trial ' || s_ot::text, 'no source');
    end if;

    if new.customer_trial_id is not null and s_ct is distinct from new.customer_trial_id then
        raise exception 'Quality test result names customer trial % but its sample % belongs to %.',
            new.customer_trial_id, new.sample_id,
            coalesce('customer trial ' || s_ct::text, 'run ' || s_run::text,
                     'optimization trial ' || s_ot::text, 'no source');
    end if;

    if new.optimization_trial_id is not null and s_ot is distinct from new.optimization_trial_id then
        raise exception 'Quality test result names optimization trial % but its sample % belongs to %.',
            new.optimization_trial_id, new.sample_id,
            coalesce('optimization trial ' || s_ot::text, 'run ' || s_run::text,
                     'customer trial ' || s_ct::text, 'no source');
    end if;

    return new;
end;
$function$;

drop trigger if exists physical_property_results_sample_run_chk
  on public.physical_property_results;

create trigger physical_property_results_sample_source_chk
  before insert or update on public.physical_property_results
  for each row execute function public.check_result_sample_matches_source();

drop function if exists public.check_result_sample_matches_run();
