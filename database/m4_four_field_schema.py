"""Migration v5: remove `directness` from the screening store (ADR-025).

The v4 store treated `directness` as one of five annotation fields and stored it
on results. The approved four-field methodology removes it. This migration:

* aborts rather than deleting any stored `directness` annotation/result;
* rebuilds `screening_annotations` and `screening_results` without the field;
* recreates the affected views, indexes and triggers with four-field semantics.

It is append-only and has not been applied to the live database.
"""

from __future__ import annotations

from database.m4_screening_schema import (
    M4_INDEXES,
    M4_TABLES,
    M4_TRIGGERS,
    M4_VIEWS,
)

M5_SCREENING_STORE_SQL = r"""
CREATE TABLE _m5_guard_preconditions (
    n INTEGER NOT NULL CHECK (n = 0)
);
INSERT INTO _m5_guard_preconditions (n)
SELECT 1
WHERE NOT EXISTS (
          SELECT 1 FROM schema_migrations WHERE version = 4)
   OR EXISTS (
          SELECT 1 FROM schema_migrations WHERE version = 5)
   OR NOT EXISTS (
          SELECT 1 FROM sqlite_master
          WHERE type = 'table' AND name = 'screening_annotations')
   OR NOT EXISTS (
          SELECT 1 FROM sqlite_master
          WHERE type = 'table' AND name = 'screening_results')
   OR EXISTS (
          SELECT 1 FROM screening_annotations WHERE field = 'directness')
   OR EXISTS (
          SELECT 1 FROM screening_results WHERE directness IS NOT NULL);
DROP TABLE _m5_guard_preconditions;

-- Defer FK enforcement until COMMIT so child tables that still reference the
-- old results table (reviews, staging events, staging results self-references)
-- do not block the parent swap. Everything is validated at COMMIT.
PRAGMA defer_foreign_keys = ON;

DROP VIEW IF EXISTS v_review_queue;

DROP VIEW IF EXISTS v_screening_evidence;

DROP VIEW IF EXISTS v_qa_coverage;

DROP VIEW IF EXISTS v_run_duplicate_counts;

DROP VIEW IF EXISTS v_screening_coverage;

DROP VIEW IF EXISTS v_run_screening;

DROP VIEW IF EXISTS v_current_screening;

DROP VIEW IF EXISTS v_pass_field_agreement;

DROP VIEW IF EXISTS v_pass_all_field_agreement;

DROP VIEW IF EXISTS v_run_two_pass_audit;

DROP VIEW IF EXISTS v_single_researcher_annotation_coverage;

DROP TRIGGER IF EXISTS trg_screening_events_completion_guard;

DROP TRIGGER IF EXISTS trg_screening_events_single_researcher_guard;

DROP TRIGGER IF EXISTS trg_screening_events_single_researcher_signoff;

DROP INDEX IF EXISTS idx_screening_results_run;

DROP INDEX IF EXISTS idx_screening_results_evidence;

DROP INDEX IF EXISTS idx_screening_results_validation;

DROP INDEX IF EXISTS idx_screening_results_run_validation;

DROP INDEX IF EXISTS idx_screening_results_review_priority;

DROP INDEX IF EXISTS ux_screening_results_one_root;

DROP INDEX IF EXISTS ux_screening_results_superseded;

DROP INDEX IF EXISTS idx_screening_annotations_evidence;

DROP INDEX IF EXISTS idx_screening_annotations_reviewer;

DROP INDEX IF EXISTS idx_screening_annotations_run_pass;

DROP TRIGGER IF EXISTS trg_screening_results_no_update;

DROP TRIGGER IF EXISTS trg_screening_results_no_delete;

DROP TRIGGER IF EXISTS trg_screening_results_run_guard;

DROP TRIGGER IF EXISTS trg_screening_results_supersede_guard;

DROP TRIGGER IF EXISTS trg_screening_results_quote_guard;

DROP TRIGGER IF EXISTS trg_screening_results_secondary_behaviours;

DROP TRIGGER IF EXISTS trg_screening_results_single_researcher_guard;

DROP TRIGGER IF EXISTS trg_screening_annotations_no_update;

DROP TRIGGER IF EXISTS trg_screening_annotations_no_delete;

DROP TRIGGER IF EXISTS trg_screening_annotations_guard;

DROP TRIGGER IF EXISTS trg_screening_annotations_quote_guard;

DROP TRIGGER IF EXISTS trg_screening_annotations_pass_guard;

DROP TRIGGER IF EXISTS trg_screening_annotations_quote_required;

DROP TRIGGER IF EXISTS trg_screening_events_no_update;

DROP TRIGGER IF EXISTS trg_screening_events_no_delete;

DROP TRIGGER IF EXISTS trg_screening_events_result_run_guard;

DROP TRIGGER IF EXISTS trg_screening_events_lifecycle_guard;

DROP INDEX IF EXISTS idx_screening_events_run;

DROP INDEX IF EXISTS idx_screening_events_result;

DROP INDEX IF EXISTS idx_screening_events_created;

CREATE TABLE _screening_results_v5 (
    screening_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                   INTEGER NOT NULL
                                 REFERENCES screening_runs(run_id)
                                 ON DELETE RESTRICT,
    evidence_id              TEXT    NOT NULL
                                 REFERENCES evidence(evidence_id)
                                 ON DELETE RESTRICT,
    evidence_content_hash    TEXT    NOT NULL CHECK (
                                 length(evidence_content_hash) = 64
                                 AND evidence_content_hash NOT GLOB '*[^0-9a-f]*'
                             ),
    duplicate_status_snapshot TEXT   NOT NULL CHECK (
                                 duplicate_status_snapshot IN (
                                     'unique', 'possible_duplicate', 'duplicate'
                                 )
                             ),
    duplicate_of_snapshot    TEXT,
    relevance                TEXT    NOT NULL CHECK (relevance IN (
                                 'relevant', 'indirectly_relevant',
                                 'unrelated', 'insufficient_information'
                             )),
    behaviour                TEXT    CHECK (
                                 behaviour IS NULL
                                 OR behaviour IN (
                                     'sip_initiation', 'sip_continuation',
                                     'missed_instalment', 'sip_reduction',
                                     'sip_pause', 'sip_cancellation',
                                     'platform_migration',
                                     'complete_discontinuation', 'unspecified'
                                 )
                             ),
    secondary_behaviours_json TEXT   NOT NULL DEFAULT '[]'
                                 CHECK (json_type(secondary_behaviours_json) = 'array'),
    first_time_status        TEXT    NOT NULL CHECK (first_time_status IN (
                                 'explicit_self_reported_first_time',
                                 'explicit_self_reported_experienced',
                                 'ambiguous_signal',
                                 'not_established'
                             )),
    sip_tenure               TEXT    NOT NULL CHECK (sip_tenure IN (
                                 'within_first_12_months_explicit',
                                 'beyond_first_12_months_explicit',
                                 'not_established'
                             )),
    confidence               REAL    CHECK (
                                 confidence IS NULL
                                 OR (confidence >= 0.0 AND confidence <= 1.0)
                             ),
    rationale                TEXT    NOT NULL
                                 CHECK (length(trim(rationale)) > 0),
    quote_text               TEXT,
    quote_start              INTEGER CHECK (
                                 quote_start IS NULL OR quote_start >= 0
                             ),
    quote_end                INTEGER CHECK (
                                 quote_end IS NULL OR quote_end >= 0
                             ),
    quote_hash               TEXT    CHECK (
                                 quote_hash IS NULL OR (
                                     length(quote_hash) = 64
                                     AND quote_hash NOT GLOB '*[^0-9a-f]*'
                                 )
                             ),
    reviewer_required        INTEGER NOT NULL DEFAULT 0
                                 CHECK (reviewer_required IN (0, 1)),
    review_priority          INTEGER CHECK (
                                 review_priority IS NULL
                                 OR review_priority BETWEEN 1 AND 5
                             ),
    review_reasons_json      TEXT    NOT NULL DEFAULT '[]'
                                 CHECK (json_type(review_reasons_json) = 'array'),
    model_metadata_json      TEXT    CHECK (
                                 model_metadata_json IS NULL
                                 OR json_type(model_metadata_json) = 'object'
                             ),
    created_by_type          TEXT    NOT NULL CHECK (created_by_type IN (
                                 'human', 'model', 'import'
                             )),
    created_by               TEXT    NOT NULL
                                 CHECK (length(trim(created_by)) > 0),
    validation_status        TEXT    NOT NULL CHECK (validation_status IN (
                                 'machine_validated', 'needs_review',
                                 'human_validated', 'rejected'
                             )),
    supersedes_screening_id  INTEGER REFERENCES screening_results(screening_id)
                                 ON DELETE RESTRICT,
    created_at               TEXT    NOT NULL,
    CHECK (supersedes_screening_id IS NULL
        OR supersedes_screening_id <> screening_id),
    CHECK (
        (relevance IN ('unrelated', 'insufficient_information')
         AND behaviour IS NULL
         AND first_time_status = 'not_established'
         AND sip_tenure = 'not_established')
        OR
        (relevance IN ('relevant', 'indirectly_relevant')
         AND behaviour IS NOT NULL)
    ),
    CHECK (
        relevance IN ('unrelated', 'insufficient_information')
        OR (quote_text IS NOT NULL AND length(trim(quote_text)) > 0)
    ),
    CHECK (
        (quote_text IS NULL AND quote_start IS NULL
         AND quote_end IS NULL AND quote_hash IS NULL)
        OR
        (quote_text IS NOT NULL AND quote_start IS NOT NULL
         AND quote_end IS NOT NULL AND quote_end > quote_start
         AND quote_hash IS NOT NULL)
    ),
    CHECK (
        relevance IN ('relevant', 'indirectly_relevant')
        OR json_array_length(secondary_behaviours_json) = 0
    ),
    -- D13: a possible duplicate may or may not carry a target. A confirmed
    -- duplicate must carry one; a unique record must not. Whatever target is
    -- stored is verified against live evidence by trg_screening_results_run_guard.
    CHECK (
        (duplicate_status_snapshot = 'unique'
         AND duplicate_of_snapshot IS NULL)
        OR
        duplicate_status_snapshot = 'possible_duplicate'
        OR
        (duplicate_status_snapshot = 'duplicate'
         AND duplicate_of_snapshot IS NOT NULL)
    ),
    CHECK (reviewer_required = 0
        OR (review_priority IS NOT NULL
            AND json_array_length(review_reasons_json) > 0)),
    CHECK (validation_status <> 'needs_review' OR reviewer_required = 1),
    CHECK (created_by_type = 'human'
        OR validation_status <> 'human_validated')
);

CREATE TABLE _screening_events_v5 (
    event_id     INTEGER,
    run_id       INTEGER NOT NULL,
    screening_id INTEGER,
    event_type   TEXT    NOT NULL,
    actor_type   TEXT    NOT NULL,
    actor_id     TEXT,
    detail_json  TEXT    NOT NULL,
    created_at   TEXT    NOT NULL
);

CREATE TABLE _screening_reviews_v5 AS
SELECT * FROM screening_reviews WHERE 0;

INSERT INTO _screening_reviews_v5
SELECT * FROM screening_reviews;

INSERT INTO _screening_results_v5 (
    screening_id,
    run_id,
    evidence_id,
    evidence_content_hash,
    duplicate_status_snapshot,
    duplicate_of_snapshot,
    relevance,
    behaviour,
    secondary_behaviours_json,
    first_time_status,
    sip_tenure,
    confidence,
    rationale,
    quote_text,
    quote_start,
    quote_end,
    quote_hash,
    reviewer_required,
    review_priority,
    review_reasons_json,
    model_metadata_json,
    created_by_type,
    created_by,
    validation_status,
    supersedes_screening_id,
    created_at)
SELECT
    screening_id,
    run_id,
    evidence_id,
    evidence_content_hash,
    duplicate_status_snapshot,
    duplicate_of_snapshot,
    relevance,
    behaviour,
    secondary_behaviours_json,
    first_time_status,
    sip_tenure,
    confidence,
    rationale,
    quote_text,
    quote_start,
    quote_end,
    quote_hash,
    reviewer_required,
    review_priority,
    review_reasons_json,
    model_metadata_json,
    created_by_type,
    created_by,
    validation_status,
    supersedes_screening_id,
    created_at
FROM screening_results;

INSERT INTO _screening_events_v5 (
    event_id,
    run_id,
    screening_id,
    event_type,
    actor_type,
    actor_id,
    detail_json,
    created_at)
SELECT
    event_id,
    run_id,
    screening_id,
    event_type,
    actor_type,
    actor_id,
    detail_json,
    created_at
FROM screening_events;

DROP TABLE screening_events;

DROP TABLE screening_results;

CREATE TABLE screening_results (
    screening_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                   INTEGER NOT NULL
                                 REFERENCES screening_runs(run_id)
                                 ON DELETE RESTRICT,
    evidence_id              TEXT    NOT NULL
                                 REFERENCES evidence(evidence_id)
                                 ON DELETE RESTRICT,
    evidence_content_hash    TEXT    NOT NULL CHECK (
                                 length(evidence_content_hash) = 64
                                 AND evidence_content_hash NOT GLOB '*[^0-9a-f]*'
                             ),
    duplicate_status_snapshot TEXT   NOT NULL CHECK (
                                 duplicate_status_snapshot IN (
                                     'unique', 'possible_duplicate', 'duplicate'
                                 )
                             ),
    duplicate_of_snapshot    TEXT,
    relevance                TEXT    NOT NULL CHECK (relevance IN (
                                 'relevant', 'indirectly_relevant',
                                 'unrelated', 'insufficient_information'
                             )),
    behaviour                TEXT    CHECK (
                                 behaviour IS NULL
                                 OR behaviour IN (
                                     'sip_initiation', 'sip_continuation',
                                     'missed_instalment', 'sip_reduction',
                                     'sip_pause', 'sip_cancellation',
                                     'platform_migration',
                                     'complete_discontinuation', 'unspecified'
                                 )
                             ),
    secondary_behaviours_json TEXT   NOT NULL DEFAULT '[]'
                                 CHECK (json_type(secondary_behaviours_json) = 'array'),
    first_time_status        TEXT    NOT NULL CHECK (first_time_status IN (
                                 'explicit_self_reported_first_time',
                                 'explicit_self_reported_experienced',
                                 'ambiguous_signal',
                                 'not_established'
                             )),
    sip_tenure               TEXT    NOT NULL CHECK (sip_tenure IN (
                                 'within_first_12_months_explicit',
                                 'beyond_first_12_months_explicit',
                                 'not_established'
                             )),
    confidence               REAL    CHECK (
                                 confidence IS NULL
                                 OR (confidence >= 0.0 AND confidence <= 1.0)
                             ),
    rationale                TEXT    NOT NULL
                                 CHECK (length(trim(rationale)) > 0),
    quote_text               TEXT,
    quote_start              INTEGER CHECK (
                                 quote_start IS NULL OR quote_start >= 0
                             ),
    quote_end                INTEGER CHECK (
                                 quote_end IS NULL OR quote_end >= 0
                             ),
    quote_hash               TEXT    CHECK (
                                 quote_hash IS NULL OR (
                                     length(quote_hash) = 64
                                     AND quote_hash NOT GLOB '*[^0-9a-f]*'
                                 )
                             ),
    reviewer_required        INTEGER NOT NULL DEFAULT 0
                                 CHECK (reviewer_required IN (0, 1)),
    review_priority          INTEGER CHECK (
                                 review_priority IS NULL
                                 OR review_priority BETWEEN 1 AND 5
                             ),
    review_reasons_json      TEXT    NOT NULL DEFAULT '[]'
                                 CHECK (json_type(review_reasons_json) = 'array'),
    model_metadata_json      TEXT    CHECK (
                                 model_metadata_json IS NULL
                                 OR json_type(model_metadata_json) = 'object'
                             ),
    created_by_type          TEXT    NOT NULL CHECK (created_by_type IN (
                                 'human', 'model', 'import'
                             )),
    created_by               TEXT    NOT NULL
                                 CHECK (length(trim(created_by)) > 0),
    validation_status        TEXT    NOT NULL CHECK (validation_status IN (
                                 'machine_validated', 'needs_review',
                                 'human_validated', 'rejected'
                             )),
    supersedes_screening_id  INTEGER REFERENCES screening_results(screening_id)
                                 ON DELETE RESTRICT,
    created_at               TEXT    NOT NULL,
    CHECK (supersedes_screening_id IS NULL
        OR supersedes_screening_id <> screening_id),
    CHECK (
        (relevance IN ('unrelated', 'insufficient_information')
         AND behaviour IS NULL
         AND first_time_status = 'not_established'
         AND sip_tenure = 'not_established')
        OR
        (relevance IN ('relevant', 'indirectly_relevant')
         AND behaviour IS NOT NULL)
    ),
    CHECK (
        relevance IN ('unrelated', 'insufficient_information')
        OR (quote_text IS NOT NULL AND length(trim(quote_text)) > 0)
    ),
    CHECK (
        (quote_text IS NULL AND quote_start IS NULL
         AND quote_end IS NULL AND quote_hash IS NULL)
        OR
        (quote_text IS NOT NULL AND quote_start IS NOT NULL
         AND quote_end IS NOT NULL AND quote_end > quote_start
         AND quote_hash IS NOT NULL)
    ),
    CHECK (
        relevance IN ('relevant', 'indirectly_relevant')
        OR json_array_length(secondary_behaviours_json) = 0
    ),
    -- D13: a possible duplicate may or may not carry a target. A confirmed
    -- duplicate must carry one; a unique record must not. Whatever target is
    -- stored is verified against live evidence by trg_screening_results_run_guard.
    CHECK (
        (duplicate_status_snapshot = 'unique'
         AND duplicate_of_snapshot IS NULL)
        OR
        duplicate_status_snapshot = 'possible_duplicate'
        OR
        (duplicate_status_snapshot = 'duplicate'
         AND duplicate_of_snapshot IS NOT NULL)
    ),
    CHECK (reviewer_required = 0
        OR (review_priority IS NOT NULL
            AND json_array_length(review_reasons_json) > 0)),
    CHECK (validation_status <> 'needs_review' OR reviewer_required = 1),
    CHECK (created_by_type = 'human'
        OR validation_status <> 'human_validated')
);

INSERT INTO screening_results (
    screening_id,
    run_id,
    evidence_id,
    evidence_content_hash,
    duplicate_status_snapshot,
    duplicate_of_snapshot,
    relevance,
    behaviour,
    secondary_behaviours_json,
    first_time_status,
    sip_tenure,
    confidence,
    rationale,
    quote_text,
    quote_start,
    quote_end,
    quote_hash,
    reviewer_required,
    review_priority,
    review_reasons_json,
    model_metadata_json,
    created_by_type,
    created_by,
    validation_status,
    supersedes_screening_id,
    created_at)
SELECT
    screening_id,
    run_id,
    evidence_id,
    evidence_content_hash,
    duplicate_status_snapshot,
    duplicate_of_snapshot,
    relevance,
    behaviour,
    secondary_behaviours_json,
    first_time_status,
    sip_tenure,
    confidence,
    rationale,
    quote_text,
    quote_start,
    quote_end,
    quote_hash,
    reviewer_required,
    review_priority,
    review_reasons_json,
    model_metadata_json,
    created_by_type,
    created_by,
    validation_status,
    supersedes_screening_id,
    created_at
FROM _screening_results_v5;

CREATE TABLE screening_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL
                     REFERENCES screening_runs(run_id)
                     ON DELETE RESTRICT,
    screening_id INTEGER REFERENCES screening_results(screening_id)
                     ON DELETE RESTRICT,
    event_type   TEXT    NOT NULL CHECK (event_type IN (
                     'run_created', 'run_started', 'run_suspended',
                     'run_resumed', 'record_screened', 'validation_failed',
                     'review_queued', 'review_completed', 'run_completed',
                     'run_aborted', 'export_created'
                 )),
    actor_type   TEXT    NOT NULL CHECK (actor_type IN (
                     'system', 'human', 'model'
                 )),
    actor_id     TEXT,
    detail_json  TEXT    NOT NULL DEFAULT '{}'
                     CHECK (json_type(detail_json) = 'object'),
    created_at   TEXT    NOT NULL,
    CHECK (actor_type <> 'human'
        OR (actor_id IS NOT NULL AND length(trim(actor_id)) > 0))
);

INSERT INTO screening_events (
    event_id,
    run_id,
    screening_id,
    event_type,
    actor_type,
    actor_id,
    detail_json,
    created_at)
SELECT
    event_id,
    run_id,
    screening_id,
    event_type,
    actor_type,
    actor_id,
    detail_json,
    created_at
FROM _screening_events_v5;

DROP TABLE _screening_results_v5;

DROP TABLE _screening_events_v5;

CREATE TABLE _screening_annotations_v5 (
    annotation_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL
                                REFERENCES screening_runs(run_id)
                                ON DELETE RESTRICT,
    evidence_id             TEXT    NOT NULL
                                REFERENCES evidence(evidence_id)
                                ON DELETE RESTRICT,
    reviewer_id             TEXT    NOT NULL
                                REFERENCES reviewers(reviewer_id)
                                ON DELETE RESTRICT,
    pass_no                 INTEGER NOT NULL DEFAULT 1
                                CHECK (pass_no IN (1, 2)),
    field                   TEXT    NOT NULL CHECK (field IN (
                                'relevance', 'behaviour',
                                'first_time_status', 'sip_tenure'
                            )),
    value                   TEXT    NOT NULL
                                CHECK (length(trim(value)) > 0),
    quote_text              TEXT,
    quote_start             INTEGER CHECK (
                                quote_start IS NULL OR quote_start >= 0
                            ),
    quote_end               INTEGER CHECK (
                                quote_end IS NULL OR quote_end >= 0
                            ),
    quote_hash              TEXT    CHECK (
                                quote_hash IS NULL OR (
                                    length(quote_hash) = 64
                                    AND quote_hash NOT GLOB '*[^0-9a-f]*'
                                )
                            ),
    blind_to_prior_pass     INTEGER NOT NULL
                                CHECK (blind_to_prior_pass IN (0, 1)),
    blind_to_other_reviewers INTEGER NOT NULL DEFAULT 1
                                CHECK (blind_to_other_reviewers IN (0, 1)),
    blind_to_model_draft    INTEGER NOT NULL DEFAULT 1
                                CHECK (blind_to_model_draft IN (0, 1)),
    independent_of_prompt_author INTEGER NOT NULL DEFAULT 1
                                CHECK (independent_of_prompt_author IN (0, 1)),
    comment                 TEXT,
    created_at              TEXT    NOT NULL
                                CHECK (julianday(created_at) IS NOT NULL)
                                CHECK (
                                    created_at GLOB
                                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                                    OR created_at GLOB
                                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                                ),
    recorded_at             TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                                CHECK (julianday(recorded_at) IS NOT NULL)
                                CHECK (
                                    recorded_at GLOB
                                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                                    OR recorded_at GLOB
                                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                                ),
    UNIQUE (run_id, evidence_id, reviewer_id, field, pass_no),
    CHECK (
        (quote_text IS NULL AND quote_start IS NULL
         AND quote_end IS NULL AND quote_hash IS NULL)
        OR
        (quote_text IS NOT NULL AND quote_start IS NOT NULL
         AND quote_end IS NOT NULL AND quote_end > quote_start
         AND quote_hash IS NOT NULL)
    )
);

INSERT INTO _screening_annotations_v5 (
    annotation_id,
    run_id,
    evidence_id,
    reviewer_id,
    pass_no,
    field,
    value,
    quote_text,
    quote_start,
    quote_end,
    quote_hash,
    blind_to_prior_pass,
    blind_to_other_reviewers,
    blind_to_model_draft,
    independent_of_prompt_author,
    comment,
    created_at,
    recorded_at)
SELECT
    annotation_id,
    run_id,
    evidence_id,
    reviewer_id,
    pass_no,
    field,
    value,
    quote_text,
    quote_start,
    quote_end,
    quote_hash,
    blind_to_prior_pass,
    blind_to_other_reviewers,
    blind_to_model_draft,
    independent_of_prompt_author,
    comment,
    created_at,
    recorded_at
FROM screening_annotations;

DROP TABLE screening_annotations;

CREATE TABLE screening_annotations (
    annotation_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                  INTEGER NOT NULL
                                REFERENCES screening_runs(run_id)
                                ON DELETE RESTRICT,
    evidence_id             TEXT    NOT NULL
                                REFERENCES evidence(evidence_id)
                                ON DELETE RESTRICT,
    reviewer_id             TEXT    NOT NULL
                                REFERENCES reviewers(reviewer_id)
                                ON DELETE RESTRICT,
    pass_no                 INTEGER NOT NULL DEFAULT 1
                                CHECK (pass_no IN (1, 2)),
    field                   TEXT    NOT NULL CHECK (field IN (
                                'relevance', 'behaviour',
                                'first_time_status', 'sip_tenure'
                            )),
    value                   TEXT    NOT NULL
                                CHECK (length(trim(value)) > 0),
    quote_text              TEXT,
    quote_start             INTEGER CHECK (
                                quote_start IS NULL OR quote_start >= 0
                            ),
    quote_end               INTEGER CHECK (
                                quote_end IS NULL OR quote_end >= 0
                            ),
    quote_hash              TEXT    CHECK (
                                quote_hash IS NULL OR (
                                    length(quote_hash) = 64
                                    AND quote_hash NOT GLOB '*[^0-9a-f]*'
                                )
                            ),
    blind_to_prior_pass     INTEGER NOT NULL
                                CHECK (blind_to_prior_pass IN (0, 1)),
    blind_to_other_reviewers INTEGER NOT NULL DEFAULT 1
                                CHECK (blind_to_other_reviewers IN (0, 1)),
    blind_to_model_draft    INTEGER NOT NULL DEFAULT 1
                                CHECK (blind_to_model_draft IN (0, 1)),
    independent_of_prompt_author INTEGER NOT NULL DEFAULT 1
                                CHECK (independent_of_prompt_author IN (0, 1)),
    comment                 TEXT,
    created_at              TEXT    NOT NULL
                                CHECK (julianday(created_at) IS NOT NULL)
                                CHECK (
                                    created_at GLOB
                                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                                    OR created_at GLOB
                                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                                ),
    recorded_at             TEXT    NOT NULL
                                DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                                CHECK (julianday(recorded_at) IS NOT NULL)
                                CHECK (
                                    recorded_at GLOB
                                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'
                                    OR recorded_at GLOB
                                        '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
                                ),
    UNIQUE (run_id, evidence_id, reviewer_id, field, pass_no),
    CHECK (
        (quote_text IS NULL AND quote_start IS NULL
         AND quote_end IS NULL AND quote_hash IS NULL)
        OR
        (quote_text IS NOT NULL AND quote_start IS NOT NULL
         AND quote_end IS NOT NULL AND quote_end > quote_start
         AND quote_hash IS NOT NULL)
    )
);

INSERT INTO screening_annotations (
    annotation_id,
    run_id,
    evidence_id,
    reviewer_id,
    pass_no,
    field,
    value,
    quote_text,
    quote_start,
    quote_end,
    quote_hash,
    blind_to_prior_pass,
    blind_to_other_reviewers,
    blind_to_model_draft,
    independent_of_prompt_author,
    comment,
    created_at,
    recorded_at)
SELECT
    annotation_id,
    run_id,
    evidence_id,
    reviewer_id,
    pass_no,
    field,
    value,
    quote_text,
    quote_start,
    quote_end,
    quote_hash,
    blind_to_prior_pass,
    blind_to_other_reviewers,
    blind_to_model_draft,
    independent_of_prompt_author,
    comment,
    created_at,
    recorded_at
FROM _screening_annotations_v5;

DROP TABLE _screening_annotations_v5;

CREATE INDEX idx_screening_annotations_evidence
    ON screening_annotations (evidence_id);

CREATE INDEX idx_screening_annotations_reviewer
    ON screening_annotations (reviewer_id);

CREATE INDEX idx_screening_annotations_run_pass
    ON screening_annotations (run_id, pass_no, evidence_id);

CREATE INDEX idx_screening_results_run
    ON screening_results (run_id);

CREATE INDEX idx_screening_results_evidence
    ON screening_results (evidence_id);

CREATE INDEX idx_screening_results_validation
    ON screening_results (validation_status);

CREATE INDEX idx_screening_results_run_validation
    ON screening_results (run_id, validation_status);

CREATE INDEX idx_screening_results_review_priority
    ON screening_results (review_priority)
    WHERE reviewer_required = 1;

-- A record has exactly one un-superseded root across all runs.
CREATE UNIQUE INDEX ux_screening_results_one_root
    ON screening_results (evidence_id)
    WHERE supersedes_screening_id IS NULL;

-- A row can be superseded at most once.
CREATE UNIQUE INDEX ux_screening_results_superseded
    ON screening_results (supersedes_screening_id)
    WHERE supersedes_screening_id IS NOT NULL;

CREATE INDEX idx_screening_events_run
    ON screening_events (run_id, event_id);

CREATE INDEX idx_screening_events_result
    ON screening_events (screening_id);

CREATE INDEX idx_screening_events_created
    ON screening_events (created_at);

CREATE UNIQUE INDEX ux_screening_events_created
    ON screening_events (run_id)
    WHERE event_type = 'run_created';

CREATE UNIQUE INDEX ux_screening_events_terminal
    ON screening_events (run_id)
    WHERE event_type = 'run_completed' OR event_type = 'run_aborted';

-- Global current: the single un-superseded, non-rejected leaf per evidence.
CREATE VIEW v_current_screening AS
SELECT r.*
FROM screening_results r
WHERE NOT EXISTS (
        SELECT 1
        FROM screening_results c
        WHERE c.supersedes_screening_id = r.screening_id
      )
  AND r.validation_status <> 'rejected';

-- Historical run readout: stable even when a later run supersedes a row.
-- Only supersessions inside the same run remove a row from this view.
CREATE VIEW v_run_screening AS
SELECT r.*
FROM screening_results r
WHERE NOT EXISTS (
        SELECT 1
        FROM screening_results c
        WHERE c.supersedes_screening_id = r.screening_id
          AND c.run_id = r.run_id
      );

-- Coverage counts for one run, including rejected outcomes.
CREATE VIEW v_screening_coverage AS
SELECT run_id,
       relevance,
       validation_status,
       COUNT(*) AS n
FROM v_run_screening
GROUP BY run_id, relevance, validation_status;

-- Raw and duplicate-collapsed counts, reproducible from result snapshots.
CREATE VIEW v_run_duplicate_counts AS
SELECT run_id,
       COUNT(*) AS raw_screened,
       COUNT(DISTINCT evidence_content_hash) AS collapsed_by_content,
       SUM(CASE WHEN duplicate_status_snapshot = 'duplicate'
                THEN 1 ELSE 0 END) AS true_duplicate_rows,
       SUM(CASE WHEN duplicate_status_snapshot = 'possible_duplicate'
                THEN 1 ELSE 0 END) AS possible_duplicate_rows,
       SUM(CASE WHEN validation_status = 'rejected'
                THEN 1 ELSE 0 END) AS rejected_rows
FROM v_run_screening
GROUP BY run_id;

-- Readout joined to immutable evidence and its origin.
-- Live duplicate status is shown beside the immutable snapshot.
CREATE VIEW v_screening_evidence AS
SELECT r.*,
       e.platform,
       e.source_type,
       e.source_locator,
       e.source_url,
       e.author,
       e.duplicate_status AS duplicate_status_live,
       s.source_name AS source_origin
FROM v_current_screening r
JOIN evidence e ON e.evidence_id = r.evidence_id
LEFT JOIN sources s ON s.source_id = e.source_id;

-- QA coverage for every full run.
CREATE VIEW v_qa_coverage AS
SELECT f.run_id AS full_run_id,
       f.screened_count,
       f.qa_required,
       q.run_id AS qa_run_id,
       (SELECT COUNT(*)
        FROM screening_samples s
        WHERE s.run_id = q.run_id AND s.purpose = 'qa') AS qa_sampled,
       (SELECT COUNT(*)
        FROM screening_results r
        WHERE r.run_id = q.run_id) AS qa_screened,
       CASE
           WHEN (SELECT COUNT(*)
                 FROM screening_samples s
                 WHERE s.run_id = q.run_id AND s.purpose = 'qa')
                >= f.qa_required
           THEN 1 ELSE 0
       END AS coverage_met
FROM screening_runs f
LEFT JOIN screening_runs q
       ON q.qa_of_run_id = f.run_id
      AND q.run_type = 'qa'
WHERE f.run_type = 'full';

-- Human review queue derived from current results.
CREATE VIEW v_review_queue AS
SELECT r.screening_id,
       r.run_id,
       r.evidence_id,
       r.review_priority,
       r.review_reasons_json,
       r.validation_status,
       r.created_at
FROM v_current_screening r
WHERE r.reviewer_required = 1
   OR r.validation_status = 'needs_review'
ORDER BY r.review_priority, r.screening_id;

CREATE VIEW v_pass_field_agreement AS
SELECT p1.run_id,
       p1.field,
       COUNT(*) AS total,
       SUM(CASE WHEN p2.value = p1.value THEN 1 ELSE 0 END) AS matches
FROM screening_annotations p1
JOIN screening_annotations p2
  ON p2.run_id = p1.run_id
 AND p2.evidence_id = p1.evidence_id
 AND p2.reviewer_id = p1.reviewer_id
 AND p2.field = p1.field
 AND p2.pass_no = 2
WHERE p1.pass_no = 1
GROUP BY p1.run_id, p1.field;

CREATE VIEW v_pass_all_field_agreement AS
SELECT run_id,
       COUNT(*) AS total,
       SUM(CASE WHEN field_count = 4 AND match_count = field_count
                THEN 1 ELSE 0 END) AS matches
FROM (
    SELECT p1.run_id,
           p1.evidence_id,
           COUNT(*) AS field_count,
           SUM(CASE WHEN p2.value = p1.value THEN 1 ELSE 0 END) AS match_count
    FROM screening_annotations p1
    JOIN screening_annotations p2
      ON p2.run_id = p1.run_id
     AND p2.evidence_id = p1.evidence_id
     AND p2.reviewer_id = p1.reviewer_id
     AND p2.field = p1.field
     AND p2.pass_no = 2
    WHERE p1.pass_no = 1
    GROUP BY p1.run_id, p1.evidence_id
)
GROUP BY run_id;

CREATE VIEW v_run_two_pass_audit AS
SELECT r.run_id,
       r.review_mode,
       (SELECT COUNT(*) FROM screening_annotations a
        WHERE a.run_id = r.run_id AND a.pass_no = 1) AS pass_1_count,
       (SELECT COUNT(*) FROM screening_annotations a
        WHERE a.run_id = r.run_id AND a.pass_no = 2) AS pass_2_count,
       (SELECT MIN(a.recorded_at) FROM screening_annotations a
        WHERE a.run_id = r.run_id AND a.pass_no = 1) AS pass_1_first_recorded_at,
       (SELECT MAX(a.recorded_at) FROM screening_annotations a
        WHERE a.run_id = r.run_id AND a.pass_no = 1) AS pass_1_last_recorded_at,
       (SELECT MIN(a.recorded_at) FROM screening_annotations a
        WHERE a.run_id = r.run_id AND a.pass_no = 2) AS pass_2_first_recorded_at,
       (SELECT MAX(a.recorded_at) FROM screening_annotations a
        WHERE a.run_id = r.run_id AND a.pass_no = 2) AS pass_2_last_recorded_at,
       CASE
           WHEN (SELECT MAX(a.recorded_at) FROM screening_annotations a
                 WHERE a.run_id = r.run_id AND a.pass_no = 1) IS NOT NULL
            AND (SELECT MIN(a.recorded_at) FROM screening_annotations a
                 WHERE a.run_id = r.run_id AND a.pass_no = 2) IS NOT NULL
           THEN ROUND((
               julianday((SELECT MIN(a.recorded_at) FROM screening_annotations a
                          WHERE a.run_id = r.run_id AND a.pass_no = 2))
               - julianday((SELECT MAX(a.recorded_at) FROM screening_annotations a
                            WHERE a.run_id = r.run_id AND a.pass_no = 1))
           ) * 24, 3)
           ELSE NULL
       END AS gap_hours,
       CASE
           WHEN (SELECT MAX(a.recorded_at) FROM screening_annotations a
                 WHERE a.run_id = r.run_id AND a.pass_no = 1) IS NOT NULL
            AND (SELECT MIN(a.recorded_at) FROM screening_annotations a
                 WHERE a.run_id = r.run_id AND a.pass_no = 2) IS NOT NULL
            AND julianday((SELECT MIN(a.recorded_at) FROM screening_annotations a
                           WHERE a.run_id = r.run_id AND a.pass_no = 2))
                - julianday((SELECT MAX(a.recorded_at) FROM screening_annotations a
                             WHERE a.run_id = r.run_id AND a.pass_no = 1)) >= 2.0
           THEN 1 ELSE 0
       END AS gap_met,
       (SELECT COUNT(DISTINCT a.reviewer_id) FROM screening_annotations a
        WHERE a.run_id = r.run_id) AS annotation_reviewers,
       (SELECT COUNT(*) FROM screening_annotations a
        WHERE a.run_id = r.run_id AND a.pass_no = 2
          AND a.blind_to_prior_pass <> 1) AS non_blind_pass_2_rows,
       (SELECT ROUND(MAX(ABS(julianday(a.recorded_at) - julianday(a.created_at))) * 86400, 0)
        FROM screening_annotations a
        WHERE a.run_id = r.run_id) AS max_clock_skew_seconds
FROM screening_runs r
WHERE r.review_mode = 'single_researcher_two_pass';

CREATE VIEW v_single_researcher_annotation_coverage AS
SELECT s.run_id,
       s.evidence_id,
       p.pass_no,
       f.field,
       CASE WHEN a.annotation_id IS NULL THEN 0 ELSE 1 END AS present
FROM screening_samples s
JOIN screening_runs r
  ON r.run_id = s.run_id
 AND r.review_mode = 'single_researcher_two_pass'
CROSS JOIN (
    SELECT 1 AS pass_no UNION ALL SELECT 2
) p
CROSS JOIN (
    SELECT 'relevance' AS field
    UNION ALL SELECT 'behaviour'
    UNION ALL SELECT 'first_time_status'
    UNION ALL SELECT 'sip_tenure'
) f
LEFT JOIN screening_annotations a
  ON a.run_id = s.run_id
 AND a.evidence_id = s.evidence_id
 AND a.pass_no = p.pass_no
 AND a.field = f.field
WHERE s.purpose = 'pilot';

CREATE TRIGGER trg_screening_annotations_no_update
BEFORE UPDATE ON screening_annotations
BEGIN
    SELECT RAISE(ABORT, 'screening_annotations are append-only');
END;

CREATE TRIGGER trg_screening_annotations_no_delete
BEFORE DELETE ON screening_annotations
BEGIN
    SELECT RAISE(ABORT, 'screening_annotations are append-only');
END;

CREATE TRIGGER trg_screening_annotations_guard
BEFORE INSERT ON screening_annotations
BEGIN
    SELECT RAISE(ABORT, 'annotations are only allowed for pilot or QA runs')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_runs
        WHERE run_id = NEW.run_id AND run_type IN ('pilot', 'qa'));

    SELECT RAISE(ABORT, 'run is not open for annotations')
    WHERE NOT EXISTS (
              SELECT 1 FROM screening_events
              WHERE run_id = NEW.run_id AND event_type = 'run_started')
       OR EXISTS (
              SELECT 1 FROM screening_events
              WHERE run_id = NEW.run_id
                AND event_type IN ('run_completed', 'run_aborted'));

    SELECT RAISE(ABORT, 'annotation evidence is outside the sealed corpus')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        JOIN screening_runs r ON r.run_id = NEW.run_id
        WHERE e.evidence_id = NEW.evidence_id
          AND e.row_id <= r.corpus_seal_row_id);

    SELECT RAISE(ABORT, 'annotation reviewer is not permitted for this run')
    WHERE NOT (
        ((SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
            = 'independent_multi_reviewer'
         AND EXISTS (
             SELECT 1 FROM reviewers
             WHERE reviewer_id = NEW.reviewer_id
               AND role IN ('reviewer', 'adjudicator')
               AND active = 1))
        OR
        ((SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
            = 'single_researcher_two_pass'
         AND NEW.reviewer_id = (
             SELECT approved_by FROM screening_runs WHERE run_id = NEW.run_id)
         AND EXISTS (
             SELECT 1 FROM reviewers
             WHERE reviewer_id = NEW.reviewer_id
               AND role = 'product_owner'
               AND active = 1))
    );

    SELECT RAISE(ABORT, 'single-researcher annotation evidence is not in the pilot sample')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND NOT EXISTS (
          SELECT 1 FROM screening_samples s
          WHERE s.run_id = NEW.run_id
            AND s.purpose = 'pilot'
            AND s.evidence_id = NEW.evidence_id);

    SELECT RAISE(ABORT, 'annotation value is outside the vocabulary for its field')
    WHERE NOT (
        (NEW.field = 'relevance' AND NEW.value IN (
            'relevant', 'indirectly_relevant',
            'unrelated', 'insufficient_information'))
        OR (NEW.field = 'behaviour' AND NEW.value IN (
            'sip_initiation', 'sip_continuation', 'missed_instalment',
            'sip_reduction', 'sip_pause', 'sip_cancellation',
            'platform_migration', 'complete_discontinuation', 'unspecified'))
        OR (NEW.field = 'first_time_status' AND NEW.value IN (
            'explicit_self_reported_first_time',
            'explicit_self_reported_experienced',
            'ambiguous_signal', 'not_established'))
        OR (NEW.field = 'sip_tenure' AND NEW.value IN (
            'within_first_12_months_explicit',
            'beyond_first_12_months_explicit', 'not_established')));
END;

CREATE TRIGGER trg_screening_annotations_quote_guard
BEFORE INSERT ON screening_annotations
WHEN NEW.quote_text IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'annotation quote offsets are required and must be ordered')
    WHERE NEW.quote_start IS NULL
       OR NEW.quote_end IS NULL
       OR NEW.quote_end <= NEW.quote_start;

    SELECT RAISE(ABORT, 'annotation quote offsets are outside the evidence text')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        WHERE e.evidence_id = NEW.evidence_id
          AND NEW.quote_end <= length(e.original_text));

    SELECT RAISE(ABORT, 'annotation quote is not an exact verbatim substring')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        WHERE e.evidence_id = NEW.evidence_id
          AND substr(e.original_text,
                     NEW.quote_start + 1,
                     NEW.quote_end - NEW.quote_start) = NEW.quote_text);
END;

CREATE TRIGGER trg_screening_annotations_pass_guard
BEFORE INSERT ON screening_annotations
BEGIN
    SELECT RAISE(ABORT, 'pass 2 is only permitted in single_researcher_two_pass runs')
    WHERE NEW.pass_no = 2
      AND (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
          <> 'single_researcher_two_pass';

    SELECT RAISE(ABORT, 'annotations are frozen after the single-researcher sign-off')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND EXISTS (
          SELECT 1 FROM screening_events
          WHERE run_id = NEW.run_id AND event_type = 'review_completed');

    SELECT RAISE(ABORT, 'pass 2 requires the matching pass 1 annotation')
    WHERE NEW.pass_no = 2
      AND NOT EXISTS (
          SELECT 1 FROM screening_annotations a
          WHERE a.run_id = NEW.run_id
            AND a.evidence_id = NEW.evidence_id
            AND a.reviewer_id = NEW.reviewer_id
            AND a.field = NEW.field
            AND a.pass_no = 1);

    SELECT RAISE(ABORT, 'pass 1 is frozen once pass 2 has begun')
    WHERE NEW.pass_no = 1
      AND (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
          = 'single_researcher_two_pass'
      AND EXISTS (
          SELECT 1 FROM screening_annotations a
          WHERE a.run_id = NEW.run_id AND a.pass_no = 2);

    SELECT RAISE(ABORT, 'the first pass 2 annotation requires a complete pass 1')
    WHERE NEW.pass_no = 2
      AND NOT EXISTS (
          SELECT 1 FROM screening_annotations a
          WHERE a.run_id = NEW.run_id AND a.pass_no = 2)
      AND (
          (SELECT COUNT(*) FROM screening_annotations a
           WHERE a.run_id = NEW.run_id AND a.pass_no = 1)
              <> (SELECT screened_count * 4 FROM screening_runs
                  WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(DISTINCT a.evidence_id) FROM screening_annotations a
           WHERE a.run_id = NEW.run_id AND a.pass_no = 1)
              <> (SELECT screened_count FROM screening_runs
                  WHERE run_id = NEW.run_id)
      );

    SELECT RAISE(ABORT, 'pass 2 must begin at least 48 hours after the last pass 1 annotation')
    WHERE NEW.pass_no = 2
      AND NOT EXISTS (
          SELECT 1 FROM screening_annotations a
          WHERE a.run_id = NEW.run_id AND a.pass_no = 2)
      AND julianday(NEW.recorded_at) - julianday(
              (SELECT MAX(a.recorded_at) FROM screening_annotations a
               WHERE a.run_id = NEW.run_id AND a.pass_no = 1)) < 2.0;

    SELECT RAISE(ABORT, 'pass 2 must set blind_to_prior_pass = 1')
    WHERE NEW.pass_no = 2 AND NEW.blind_to_prior_pass <> 1;

    SELECT RAISE(ABORT, 'pass 1 has no prior pass and must set blind_to_prior_pass = 1')
    WHERE NEW.pass_no = 1 AND NEW.blind_to_prior_pass <> 1;
END;

CREATE TRIGGER trg_screening_annotations_quote_required
BEFORE INSERT ON screening_annotations
WHEN NEW.field = 'relevance'
 AND NEW.value IN ('relevant', 'indirectly_relevant')
BEGIN
    SELECT RAISE(ABORT, 'relevant annotations require quote text, offsets and hash')
    WHERE NEW.quote_text IS NULL
       OR length(trim(NEW.quote_text)) = 0
       OR NEW.quote_start IS NULL
       OR NEW.quote_end IS NULL
       OR NEW.quote_hash IS NULL;
END;

CREATE TRIGGER trg_screening_results_no_update
BEFORE UPDATE ON screening_results
BEGIN
    SELECT RAISE(ABORT, 'screening_results are append-only; supersede instead');
END;

CREATE TRIGGER trg_screening_results_no_delete
BEFORE DELETE ON screening_results
BEGIN
    SELECT RAISE(ABORT, 'screening_results are append-only');
END;

CREATE TRIGGER trg_screening_results_run_guard
BEFORE INSERT ON screening_results
BEGIN
    SELECT RAISE(ABORT, 'run is not open for screening')
    WHERE NOT EXISTS (
              SELECT 1 FROM screening_events
              WHERE run_id = NEW.run_id AND event_type = 'run_started')
       OR EXISTS (
              SELECT 1 FROM screening_events
              WHERE run_id = NEW.run_id
                AND event_type IN ('run_completed', 'run_aborted'));

    SELECT RAISE(ABORT, 'evidence is outside the sealed corpus')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        JOIN screening_runs r ON r.run_id = NEW.run_id
        WHERE e.evidence_id = NEW.evidence_id
          AND e.row_id <= r.corpus_seal_row_id);

    SELECT RAISE(ABORT, 'evidence content hash does not match the stored evidence')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        WHERE e.evidence_id = NEW.evidence_id
          AND e.content_hash = NEW.evidence_content_hash);

    SELECT RAISE(ABORT, 'duplicate snapshot does not match the stored evidence at insert')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        WHERE e.evidence_id = NEW.evidence_id
          AND e.duplicate_status = NEW.duplicate_status_snapshot
          -- D13: a stored target must equal the live target. A NULL target is
          -- allowed when the live record has none or the live status is
          -- 'possible_duplicate'; the table CHECK already forbids NULL for
          -- 'duplicate' and any target for 'unique'.
          AND (NEW.duplicate_of_snapshot IS NULL
               OR e.duplicate_of IS NEW.duplicate_of_snapshot));
END;

CREATE TRIGGER trg_screening_results_supersede_guard
BEFORE INSERT ON screening_results
WHEN NEW.supersedes_screening_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'superseded row must exist for the same evidence')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_results p
        WHERE p.screening_id = NEW.supersedes_screening_id
          AND p.evidence_id = NEW.evidence_id);

    SELECT RAISE(ABORT, 'result has already been superseded')
    WHERE EXISTS (
        SELECT 1 FROM screening_results c
        WHERE c.supersedes_screening_id = NEW.supersedes_screening_id);
END;

CREATE TRIGGER trg_screening_results_quote_guard
BEFORE INSERT ON screening_results
WHEN NEW.quote_text IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'quote offsets are required and must be ordered')
    WHERE NEW.quote_start IS NULL
       OR NEW.quote_end IS NULL
       OR NEW.quote_end <= NEW.quote_start;

    SELECT RAISE(ABORT, 'quote offsets are outside the evidence text')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        WHERE e.evidence_id = NEW.evidence_id
          AND NEW.quote_end <= length(e.original_text));

    SELECT RAISE(ABORT, 'quote is not an exact verbatim substring')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        WHERE e.evidence_id = NEW.evidence_id
          AND substr(e.original_text,
                     NEW.quote_start + 1,
                     NEW.quote_end - NEW.quote_start) = NEW.quote_text);
END;

CREATE TRIGGER trg_screening_results_secondary_behaviours
BEFORE INSERT ON screening_results
WHEN json_array_length(NEW.secondary_behaviours_json) > 0
BEGIN
    SELECT RAISE(ABORT, 'secondary behaviour outside the vocabulary or duplicating the primary')
    WHERE EXISTS (
        SELECT 1
        FROM json_each(NEW.secondary_behaviours_json) AS j
        WHERE j.type <> 'text'
           OR j.value NOT IN (
                'sip_initiation', 'sip_continuation', 'missed_instalment',
                'sip_reduction', 'sip_pause', 'sip_cancellation',
                'platform_migration', 'complete_discontinuation', 'unspecified')
           OR j.value = NEW.behaviour);
END;

CREATE TRIGGER trg_screening_results_single_researcher_guard
BEFORE INSERT ON screening_results
BEGIN
    SELECT RAISE(ABORT, 'single_researcher_two_pass results must be human-authored by the approved researcher')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          NEW.created_by_type <> 'human'
          OR NEW.created_by <> (
              SELECT approved_by FROM screening_runs WHERE run_id = NEW.run_id)
          OR NOT EXISTS (
              SELECT 1 FROM reviewers
              WHERE reviewer_id = NEW.created_by
                AND role = 'product_owner'
                AND active = 1)
          OR NEW.confidence IS NOT NULL
          OR NEW.model_metadata_json IS NOT NULL
          OR NEW.validation_status NOT IN ('human_validated', 'rejected')
          OR NEW.reviewer_required <> 0
          OR NEW.review_priority IS NOT NULL
          OR json_array_length(NEW.review_reasons_json) <> 0
      );

    SELECT RAISE(ABORT, 'single_researcher_two_pass results must be for a pilot sample')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND NOT EXISTS (
          SELECT 1 FROM screening_samples s
          WHERE s.run_id = NEW.run_id
            AND s.purpose = 'pilot'
            AND s.evidence_id = NEW.evidence_id);

    SELECT RAISE(ABORT, 'results are frozen after the single-researcher sign-off')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND EXISTS (
          SELECT 1 FROM screening_events
          WHERE run_id = NEW.run_id AND event_type = 'review_completed');
END;

CREATE TRIGGER trg_screening_events_no_update
BEFORE UPDATE ON screening_events
BEGIN
    SELECT RAISE(ABORT, 'screening_events are append-only');
END;

CREATE TRIGGER trg_screening_events_no_delete
BEFORE DELETE ON screening_events
BEGIN
    SELECT RAISE(ABORT, 'screening_events are append-only');
END;

CREATE TRIGGER trg_screening_events_result_run_guard
BEFORE INSERT ON screening_events
WHEN NEW.screening_id IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'event result does not belong to the event run')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_results
        WHERE screening_id = NEW.screening_id
          AND run_id = NEW.run_id);
END;

CREATE TRIGGER trg_screening_events_lifecycle_guard
BEFORE INSERT ON screening_events
BEGIN
    SELECT RAISE(ABORT, 'run_created must be the first event for a run')
    WHERE NEW.event_type <> 'run_created'
      AND NOT EXISTS (
          SELECT 1 FROM screening_events
          WHERE run_id = NEW.run_id AND event_type = 'run_created');

    SELECT RAISE(ABORT, 'run_started requires run_created')
    WHERE NEW.event_type = 'run_started'
      AND NOT EXISTS (
          SELECT 1 FROM screening_events
          WHERE run_id = NEW.run_id AND event_type = 'run_created');

    SELECT RAISE(ABORT, 'human actor must be a registered reviewer')
    WHERE NEW.actor_type = 'human'
      AND NOT EXISTS (
          SELECT 1 FROM reviewers
          WHERE reviewer_id = NEW.actor_id AND active = 1);

    SELECT RAISE(ABORT, 'cannot resume a completed or aborted run')
    WHERE NEW.event_type = 'run_resumed'
      AND EXISTS (
          SELECT 1 FROM screening_events
          WHERE run_id = NEW.run_id
            AND event_type IN ('run_completed', 'run_aborted'));
END;

CREATE TRIGGER trg_screening_events_single_researcher_guard
BEFORE INSERT ON screening_events
BEGIN
    SELECT RAISE(ABORT, 'human events in a single_researcher_two_pass run must be authored by the approved researcher')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND NEW.actor_type = 'human'
      AND NEW.actor_id IS NOT (
          SELECT approved_by FROM screening_runs WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'review_completed in a single_researcher_two_pass run must be the approved researcher')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND NEW.event_type = 'review_completed'
      AND (
          NEW.actor_type <> 'human'
          OR NEW.actor_id IS NOT (
              SELECT approved_by FROM screening_runs WHERE run_id = NEW.run_id)
      );
END;

CREATE TRIGGER trg_screening_events_completion_guard
BEFORE INSERT ON screening_events
WHEN NEW.event_type = 'run_completed'
BEGIN
    SELECT RAISE(ABORT, 'run must be started before completion')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_events
        WHERE run_id = NEW.run_id AND event_type = 'run_started');

    SELECT RAISE(ABORT, 'screened record count does not match the run manifest')
    WHERE (SELECT screened_count FROM screening_runs WHERE run_id = NEW.run_id)
          <> (SELECT COUNT(DISTINCT evidence_id) FROM screening_results
              WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'pilot/QA sample membership does not match screened_count')
    WHERE (SELECT run_type FROM screening_runs WHERE run_id = NEW.run_id)
          IN ('pilot', 'qa')
      AND (SELECT COUNT(*) FROM screening_samples WHERE run_id = NEW.run_id)
          <> (SELECT screened_count FROM screening_runs WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'pilot completion requires every sampled record to be human-validated')
    WHERE (SELECT run_type FROM screening_runs WHERE run_id = NEW.run_id) = 'pilot'
      AND EXISTS (
          SELECT 1 FROM screening_samples s
          WHERE s.run_id = NEW.run_id AND s.purpose = 'pilot'
            AND NOT EXISTS (
                SELECT 1 FROM screening_results r
                WHERE r.run_id = NEW.run_id
                  AND r.evidence_id = s.evidence_id
                  AND r.validation_status = 'human_validated'
                  AND NOT EXISTS (
                      SELECT 1 FROM screening_results c
                      WHERE c.supersedes_screening_id = r.screening_id)));

    SELECT RAISE(ABORT, 'every QA sample must be screened in the QA run')
    WHERE (SELECT run_type FROM screening_runs WHERE run_id = NEW.run_id) = 'qa'
      AND EXISTS (
          SELECT 1 FROM screening_samples s
          WHERE s.run_id = NEW.run_id AND s.purpose = 'qa'
            AND NOT EXISTS (
                SELECT 1 FROM screening_results r
                WHERE r.run_id = NEW.run_id
                  AND r.evidence_id = s.evidence_id));

    SELECT RAISE(ABORT, 'full run requires a completed QA run meeting the coverage floor')
    WHERE (SELECT run_type FROM screening_runs WHERE run_id = NEW.run_id) = 'full'
      AND NOT EXISTS (
          SELECT 1
          FROM screening_runs q
          JOIN screening_events qe
            ON qe.run_id = q.run_id
           AND qe.event_type = 'run_completed'
          WHERE q.run_type = 'qa'
            AND q.qa_of_run_id = NEW.run_id
            AND (SELECT COUNT(*) FROM screening_samples s
                 WHERE s.run_id = q.run_id AND s.purpose = 'qa')
                >= (SELECT qa_required FROM screening_runs
                    WHERE run_id = NEW.run_id));

    SELECT RAISE(ABORT, 'single-researcher completion requires exactly one annotation reviewer')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (SELECT COUNT(DISTINCT reviewer_id) FROM screening_annotations
           WHERE run_id = NEW.run_id) <> 1;

    SELECT RAISE(ABORT, 'single-researcher annotations must be authored by the approved researcher')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND EXISTS (
          SELECT 1 FROM screening_annotations a
          WHERE a.run_id = NEW.run_id
            AND a.reviewer_id <> (
                SELECT approved_by FROM screening_runs WHERE run_id = NEW.run_id));

    SELECT RAISE(ABORT, 'single-researcher completion requires complete pass 1')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          (SELECT COUNT(*) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 1)
              <> (SELECT screened_count * 4 FROM screening_runs
                  WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(DISTINCT evidence_id) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 1)
              <> (SELECT screened_count FROM screening_runs
                  WHERE run_id = NEW.run_id)
      );

    SELECT RAISE(ABORT, 'single-researcher completion requires complete pass 2')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          (SELECT COUNT(*) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2)
              <> (SELECT screened_count * 4 FROM screening_runs
                  WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(DISTINCT evidence_id) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2)
              <> (SELECT screened_count FROM screening_runs
                  WHERE run_id = NEW.run_id)
      );

    SELECT RAISE(ABORT, 'single-researcher completion requires all four fields in both passes for every sampled record')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND EXISTS (
          SELECT 1
          FROM screening_samples s
          CROSS JOIN (
              SELECT 1 AS pass_no UNION ALL SELECT 2
          ) p
          CROSS JOIN (
              SELECT 'relevance' AS field
              UNION ALL SELECT 'behaviour'
              UNION ALL SELECT 'first_time_status'
              UNION ALL SELECT 'sip_tenure'
          ) f
          WHERE s.run_id = NEW.run_id
            AND s.purpose = 'pilot'
            AND NOT EXISTS (
                SELECT 1 FROM screening_annotations a
                WHERE a.run_id = s.run_id
                  AND a.evidence_id = s.evidence_id
                  AND a.pass_no = p.pass_no
                  AND a.field = f.field)
      );

    SELECT RAISE(ABORT, 'single-researcher completion requires a reconciliation sign-off')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND NOT EXISTS (
          SELECT 1 FROM screening_events
          WHERE run_id = NEW.run_id AND event_type = 'review_completed');

    SELECT RAISE(ABORT, 'single-researcher completion requires a 48-hour gap')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND COALESCE(
          (SELECT julianday(MIN(recorded_at)) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2)
          -
          (SELECT julianday(MAX(recorded_at)) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 1),
          0) < 2.0;
END;

CREATE TRIGGER trg_screening_events_single_researcher_signoff
BEFORE INSERT ON screening_events
WHEN NEW.event_type = 'review_completed'
BEGIN
    SELECT RAISE(ABORT, 'single-researcher run already has a reconciliation sign-off')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND EXISTS (
          SELECT 1 FROM screening_events
          WHERE run_id = NEW.run_id AND event_type = 'review_completed');

    SELECT RAISE(ABORT, 'single-researcher sign-off requires every sampled record to have a human-validated leaf')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND EXISTS (
          SELECT 1 FROM screening_samples s
          WHERE s.run_id = NEW.run_id AND s.purpose = 'pilot'
            AND NOT EXISTS (
                SELECT 1 FROM screening_results r
                WHERE r.run_id = NEW.run_id
                  AND r.evidence_id = s.evidence_id
                  AND r.validation_status = 'human_validated'
                  AND NOT EXISTS (
                      SELECT 1 FROM screening_results c
                      WHERE c.supersedes_screening_id = r.screening_id)));

    SELECT RAISE(ABORT, 'single-researcher sign-off pass counts do not match stored annotations')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          json_extract(NEW.detail_json, '$.pass_1_count')
              IS NOT (SELECT COUNT(*) FROM screening_annotations
                      WHERE run_id = NEW.run_id AND pass_no = 1)
          OR
          json_extract(NEW.detail_json, '$.pass_2_count')
              IS NOT (SELECT COUNT(*) FROM screening_annotations
                      WHERE run_id = NEW.run_id AND pass_no = 2)
      );

    SELECT RAISE(ABORT, 'single-researcher sign-off requires complete pass 1 and pass 2')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          (SELECT COUNT(*) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 1)
              IS NOT (SELECT screened_count * 4 FROM screening_runs
                      WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(DISTINCT evidence_id) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 1)
              IS NOT (SELECT screened_count FROM screening_runs
                      WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(*) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2)
              IS NOT (SELECT screened_count * 4 FROM screening_runs
                      WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(DISTINCT evidence_id) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2)
              IS NOT (SELECT screened_count FROM screening_runs
                      WHERE run_id = NEW.run_id)
      );

    SELECT RAISE(ABORT, 'relevant/indirect annotations require quotes for every field')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND EXISTS (
          SELECT 1
          FROM screening_annotations a
          JOIN screening_annotations rel
            ON rel.run_id = a.run_id
           AND rel.evidence_id = a.evidence_id
           AND rel.reviewer_id = a.reviewer_id
           AND rel.pass_no = a.pass_no
           AND rel.field = 'relevance'
          WHERE a.run_id = NEW.run_id
            AND rel.value IN ('relevant', 'indirectly_relevant')
            AND (a.quote_text IS NULL
                 OR length(trim(a.quote_text)) = 0
                 OR a.quote_start IS NULL
                 OR a.quote_end IS NULL
                 OR a.quote_hash IS NULL)
      );

    SELECT RAISE(ABORT, 'single-researcher sign-off timestamps do not match stored annotations')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          json_extract(NEW.detail_json, '$.pass_1_last_recorded_at')
              IS NOT (SELECT MAX(recorded_at) FROM screening_annotations
                      WHERE run_id = NEW.run_id AND pass_no = 1)
          OR
          json_extract(NEW.detail_json, '$.pass_2_first_recorded_at')
              IS NOT (SELECT MIN(recorded_at) FROM screening_annotations
                      WHERE run_id = NEW.run_id AND pass_no = 2)
      );

    SELECT RAISE(ABORT, 'single-researcher sign-off must report complete per-field agreement')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          COALESCE(json_type(NEW.detail_json, '$.agreement'), 'missing') <> 'object'
          OR (SELECT COUNT(*) FROM json_each(NEW.detail_json, '$.agreement')) <> 5
          OR (SELECT COUNT(*) FROM json_each(NEW.detail_json, '$.agreement')
              WHERE key IN (
                  'relevance', 'behaviour',
                  'first_time_status', 'sip_tenure', 'all_four_fields')) <> 5
          OR EXISTS (
              SELECT 1
              FROM json_each(NEW.detail_json, '$.agreement') AS f
              WHERE json_type(f.value, '$.matches') IS NOT 'integer'
                 OR json_type(f.value, '$.total') IS NOT 'integer'
                 OR json_extract(f.value, '$.total')
                      IS NOT (SELECT screened_count FROM screening_runs
                              WHERE run_id = NEW.run_id)
                 OR json_extract(f.value, '$.matches') < 0
                 OR json_extract(f.value, '$.matches')
                      > json_extract(f.value, '$.total'))
      );

    SELECT RAISE(ABORT, 'single-researcher sign-off agreement does not match stored annotations')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND EXISTS (
          SELECT 1
          FROM v_pass_field_agreement v
          WHERE v.run_id = NEW.run_id
            AND (
                json_extract(NEW.detail_json,
                             '$.agreement.' || v.field || '.matches')
                    IS NOT v.matches
                OR
                json_extract(NEW.detail_json,
                             '$.agreement.' || v.field || '.total')
                    IS NOT v.total
            ));

    SELECT RAISE(ABORT, 'single-researcher sign-off all-four-fields agreement does not match stored annotations')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          (SELECT json_extract(NEW.detail_json,
                               '$.agreement.all_four_fields.matches'))
              IS NOT (SELECT matches FROM v_pass_all_field_agreement
                      WHERE run_id = NEW.run_id)
          OR
          (SELECT json_extract(NEW.detail_json,
                               '$.agreement.all_four_fields.total'))
              IS NOT (SELECT total FROM v_pass_all_field_agreement
                      WHERE run_id = NEW.run_id)
      );

    SELECT RAISE(ABORT, 'single-researcher sign-off changed-record count does not match stored annotations')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND json_extract(NEW.detail_json, '$.changed_records')
          IS NOT (
              SELECT COUNT(*) FROM (
                  SELECT p1.evidence_id
                  FROM screening_annotations p1
                  JOIN screening_annotations p2
                    ON p2.run_id = p1.run_id
                   AND p2.evidence_id = p1.evidence_id
                   AND p2.reviewer_id = p1.reviewer_id
                   AND p2.field = p1.field
                   AND p2.pass_no = 2
                  WHERE p1.run_id = NEW.run_id
                    AND p1.pass_no = 1
                    AND p2.value <> p1.value
                  GROUP BY p1.evidence_id
              )
          );

    SELECT RAISE(ABORT, 'single-researcher sign-off reconciliation report is incomplete or claims independent validation')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          json_extract(NEW.detail_json, '$.reconciled_records')
              IS NOT json_extract(NEW.detail_json, '$.changed_records')
          OR json_type(NEW.detail_json, '$.reconciled_evidence_ids') <> 'array'
          OR json_array_length(NEW.detail_json, '$.reconciled_evidence_ids')
              IS NOT json_extract(NEW.detail_json, '$.changed_records')
          OR EXISTS (
              SELECT 1 FROM json_each(NEW.detail_json, '$.reconciled_evidence_ids')
              WHERE type <> 'text')
          OR (SELECT COUNT(*) FROM json_each(NEW.detail_json, '$.reconciled_evidence_ids'))
             <> (SELECT COUNT(DISTINCT value)
                 FROM json_each(NEW.detail_json, '$.reconciled_evidence_ids'))
          OR EXISTS (
              SELECT value
              FROM json_each(NEW.detail_json, '$.reconciled_evidence_ids')
              EXCEPT
              SELECT p1.evidence_id
              FROM screening_annotations p1
              JOIN screening_annotations p2
                ON p2.run_id = p1.run_id
               AND p2.evidence_id = p1.evidence_id
               AND p2.reviewer_id = p1.reviewer_id
               AND p2.field = p1.field
               AND p2.pass_no = 2
              WHERE p1.run_id = NEW.run_id
                AND p1.pass_no = 1
                AND p2.value <> p1.value
          )
          OR EXISTS (
              SELECT p1.evidence_id
              FROM screening_annotations p1
              JOIN screening_annotations p2
                ON p2.run_id = p1.run_id
               AND p2.evidence_id = p1.evidence_id
               AND p2.reviewer_id = p1.reviewer_id
               AND p2.field = p1.field
               AND p2.pass_no = 2
              WHERE p1.run_id = NEW.run_id
                AND p1.pass_no = 1
                AND p2.value <> p1.value
              GROUP BY p1.evidence_id
              EXCEPT
              SELECT value
              FROM json_each(NEW.detail_json, '$.reconciled_evidence_ids')
          )
          OR COALESCE(json_extract(NEW.detail_json, '$.unresolved_records'), -1) <> 0
          OR json_type(NEW.detail_json, '$.quote_hash_audit_mismatches') IS NOT 'integer'
          OR COALESCE(json_extract(NEW.detail_json,
                                   '$.quote_hash_audit_mismatches'), -1) <> 0
          OR COALESCE(json_extract(NEW.detail_json,
                                   '$.independent_validation_performed'), -1) <> 0
          OR COALESCE(json_extract(NEW.detail_json,
                                   '$.inter_rater_reliability_claimed'), -1) <> 0
          OR COALESCE(length(trim(json_extract(NEW.detail_json,
                          '$.unresolved_ambiguities'))), 0) = 0
          OR COALESCE(length(trim(json_extract(NEW.detail_json,
                          '$.agreement_interpretation'))), 0) = 0
          OR COALESCE(length(trim(json_extract(NEW.detail_json,
                          '$.bias_statement'))), 0) = 0
          OR COALESCE(length(trim(json_extract(NEW.detail_json,
                          '$.limitations'))), 0) = 0
      );
END;

-- Explicit review guard: reviews are not rebuilt, so prove that none were
-- lost and that every result reference (including replacements) still exists.
CREATE TABLE _m5_review_guard (
    n INTEGER NOT NULL CHECK (n = 0)
);
INSERT INTO _m5_review_guard (n)
SELECT 1
WHERE (SELECT COUNT(*) FROM screening_reviews)
          <> (SELECT COUNT(*) FROM _screening_reviews_v5)
   OR EXISTS (
       SELECT 1 FROM screening_reviews r
       LEFT JOIN screening_results s ON s.screening_id = r.screening_id
       WHERE s.screening_id IS NULL)
   OR EXISTS (
       SELECT 1 FROM screening_reviews r
       WHERE r.replacement_screening_id IS NOT NULL
         AND NOT EXISTS (
             SELECT 1 FROM screening_results s
             WHERE s.screening_id = r.replacement_screening_id));
DROP TABLE _m5_review_guard;
DROP TABLE _screening_reviews_v5;
"""

# The object inventory is unchanged: the migration rebuilds existing objects.
M5_TABLES: tuple[str, ...] = M4_TABLES
M5_VIEWS: tuple[str, ...] = M4_VIEWS
M5_INDEXES: tuple[str, ...] = M4_INDEXES
M5_TRIGGERS: tuple[str, ...] = M4_TRIGGERS
M5_RECREATED_TRIGGERS: tuple[str, ...] = (
    "trg_screening_annotations_guard",
    "trg_screening_annotations_quote_guard",
    "trg_screening_annotations_no_update",
    "trg_screening_annotations_no_delete",
    "trg_screening_annotations_pass_guard",
    "trg_screening_annotations_quote_required",
    "trg_screening_results_no_update",
    "trg_screening_results_no_delete",
    "trg_screening_results_run_guard",
    "trg_screening_results_supersede_guard",
    "trg_screening_results_quote_guard",
    "trg_screening_results_secondary_behaviours",
    "trg_screening_results_single_researcher_guard",
    "trg_screening_events_completion_guard",
    "trg_screening_events_single_researcher_guard",
    "trg_screening_events_single_researcher_signoff",
)

__all__ = [
    "M5_INDEXES",
    "M5_SCREENING_STORE_SQL",
    "M5_TABLES",
    "M5_TRIGGERS",
    "M5_VIEWS",
]
