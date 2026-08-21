-- REACH R-A1, revised after Charlie's stop-point review of 21 Aug 2026.
--
-- Activation must require that the source was checked, and that check must be
-- ATTRIBUTABLE and DATED. source_checked_date already recorded the date. Who
-- recorded it did not exist as a field of its own: loaded_by says who ran the
-- load, which is a different claim. The person who confirms a file is the
-- current official one is making a statement PI3 relies on, and an unsigned
-- statement is not evidence.
-- @count: regulatory_reference_sets
-- @expect: regulatory_reference_sets +0

alter table public.regulatory_reference_sets
  add column source_checked_by varchar(200);

-- An active dataset must carry complete provenance: who confirmed the source
-- and when, and where the immutable original is retained. Enforced here as
-- well as in the application so no code path can activate a dataset that
-- cannot be traced to the file it came from.
alter table public.regulatory_reference_sets
  add constraint ck_regref_active_provenance_complete
  check (
    is_active is not true
    or (source_checked_date is not null
        and source_checked_by is not null
        and storage_object_key is not null
        and storage_bucket is not null
        and file_hash is not null)
  );
