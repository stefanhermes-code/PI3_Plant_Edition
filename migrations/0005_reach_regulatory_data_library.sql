-- REACH work package R-A1: the Regulatory Data Library.
--
-- Extends the reference store that shipped at v2.25.0 rather than creating a
-- second one. Charlie's ruling of 21 Aug: two tables for one idea would let a
-- CertiPUR lookup and a REACH lookup disagree about the same file.
--
-- Both tables are empty at the time of this migration, so reference_type is
-- RENAMED to dataset_slot rather than shadowed by a second column meaning the
-- same thing. dataset_slot holds a short controlled key from
-- regulatory_reference.DATASET_SLOTS, not a display label - a label that
-- changes must never orphan the rows loaded under it.
-- @count: regulatory_reference_sets, regulatory_reference_records
-- @expect: regulatory_reference_sets +0
-- @expect: regulatory_reference_records +0

alter table public.regulatory_reference_sets
  rename column reference_type to dataset_slot;

-- Where the immutable original lives. The bytes are NOT held in this table:
-- an official regulatory file is megabytes, every row of it is already parsed
-- into regulatory_reference_records, and the original is kept for provenance
-- rather than for reading.
alter table public.regulatory_reference_sets
  add column storage_backend    varchar(40),
  add column storage_bucket     varchar(120),
  add column storage_object_key varchar(400),
  add column file_size          integer;

-- The activation workflow. is_active alone cannot answer "who activated this,
-- when, and what did it replace" - which is the whole question an auditor asks
-- about a regulatory dataset.
alter table public.regulatory_reference_sets
  add column status               varchar(20) not null default 'active',
  add column activated_at         timestamptz,
  add column activated_by         varchar(200),
  add column superseded_at        timestamptz,
  add column superseded_by_set_id integer references public.regulatory_reference_sets(id);

alter table public.regulatory_reference_sets
  add constraint ck_regref_status
  check (status in ('active', 'superseded'));

-- A superseded set must say what replaced it, and an active one must not.
alter table public.regulatory_reference_sets
  add constraint ck_regref_status_matches_flag
  check ((status = 'active' and is_active is true and superseded_at is null)
      or (status = 'superseded' and is_active is not true));

-- One active dataset per regulatory slot. Replaces the per-type index, which
-- named a column that no longer exists.
drop index if exists ux_regref_one_active_per_type;

create unique index ux_regref_one_active_per_slot
  on public.regulatory_reference_sets (dataset_slot)
  where is_active is true;

-- Duplicate detection by file hash, at the database rather than only in code.
-- Loading the same official file twice into the same slot is not a new version,
-- it is the same version loaded again, and it must be refused rather than
-- silently creating a second set nobody can tell apart.
create unique index ux_regref_slot_file_hash
  on public.regulatory_reference_sets (dataset_slot, file_hash);

create index ix_regref_sets_slot on public.regulatory_reference_sets (dataset_slot);
