"""Migration v3 schema: the M3 append-only screening store.

This module is the executable form of the approved Revision 2 schema in
``docs/M3_EXECUTION_PLAN.md`` section 5. It is imported by
``database.migrations`` as the version-3 migration script.

The script is intentionally ordered:

1. the legacy-classification guard (aborts if any frozen column is set),
2. the nine screening-store tables,
3. the seven read-model views,
4. the twenty-one explicit indexes,
5. the thirty-three append-only, integrity and freeze triggers.

It is additive only. It does not alter ``evidence`` and does not backfill any
screening, reviewer, prompt or run data.
"""

M3_SCREENING_STORE_SQL = r"""
CREATE TABLE _m3_guard_legacy_classification (
    n INTEGER NOT NULL CHECK (n = 0)
);
INSERT INTO _m3_guard_legacy_classification (n)
SELECT COUNT(*) FROM evidence
WHERE relevance IS NOT NULL
   OR behaviour IS NOT NULL
   OR directness IS NOT NULL
   OR confidence IS NOT NULL;
DROP TABLE _m3_guard_legacy_classification;

CREATE TABLE reviewers (
    reviewer_id     TEXT PRIMARY KEY
                        CHECK (length(trim(reviewer_id)) > 0),
    display_name    TEXT NOT NULL
                        CHECK (length(trim(display_name)) > 0),
    role            TEXT NOT NULL CHECK (role IN (
                        'reviewer', 'adjudicator', 'product_owner'
                    )),
    active          INTEGER NOT NULL DEFAULT 1
                        CHECK (active IN (0, 1)),
    created_at      TEXT NOT NULL
);

CREATE TABLE prompts (
    prompt_version  TEXT PRIMARY KEY
                        CHECK (length(trim(prompt_version)) > 0),
    prompt_sha256   TEXT NOT NULL CHECK (
                        length(prompt_sha256) = 64
                        AND prompt_sha256 NOT GLOB '*[^0-9a-f]*'
                    ),
    prompt_text     TEXT NOT NULL
                        CHECK (length(trim(prompt_text)) > 0),
    created_at      TEXT NOT NULL,
    created_by      TEXT NOT NULL
                        CHECK (length(trim(created_by)) > 0),
    notes           TEXT
);

CREATE TABLE screening_runs (
    run_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid                TEXT    NOT NULL UNIQUE
                                CHECK (length(run_uuid) >= 16),
    run_type                TEXT    NOT NULL CHECK (run_type IN (
                                'pilot', 'full', 'qa'
                            )),
    created_at              TEXT    NOT NULL,
    created_by              TEXT    NOT NULL
                                CHECK (length(trim(created_by)) > 0),
    label_version           TEXT    NOT NULL
                                CHECK (length(trim(label_version)) > 0),
    schema_version          INTEGER NOT NULL CHECK (schema_version >= 3),
    code_commit             TEXT    NOT NULL
                                CHECK (length(trim(code_commit)) > 0),
    corpus_seal_row_id      INTEGER NOT NULL CHECK (corpus_seal_row_id > 0),
    corpus_sha256           TEXT    NOT NULL CHECK (
                                length(corpus_sha256) = 64
                                AND corpus_sha256 NOT GLOB '*[^0-9a-f]*'
                            ),
    corpus_digest_sha256    TEXT    NOT NULL CHECK (
                                length(corpus_digest_sha256) = 64
                                AND corpus_digest_sha256 NOT GLOB '*[^0-9a-f]*'
                            ),
    evidence_count          INTEGER NOT NULL CHECK (evidence_count >= 0),
    genuine_count           INTEGER NOT NULL CHECK (genuine_count >= 0),
    screened_count          INTEGER NOT NULL CHECK (screened_count > 0),
    qa_required             INTEGER NOT NULL CHECK (qa_required >= 30),
    confidence_threshold    REAL    NOT NULL CHECK (
                                confidence_threshold >= 0.0
                                AND confidence_threshold <= 1.0
                            ),
    sample_seed             INTEGER,
    sample_size             INTEGER CHECK (
                                sample_size IS NULL OR sample_size > 0
                            ),
    frame_sha256            TEXT    CHECK (
                                frame_sha256 IS NULL OR (
                                    length(frame_sha256) = 64
                                    AND frame_sha256 NOT GLOB '*[^0-9a-f]*'
                                )
                            ),
    selection_algorithm     TEXT,
    lexicon_sha256          TEXT    CHECK (
                                lexicon_sha256 IS NULL OR (
                                    length(lexicon_sha256) = 64
                                    AND lexicon_sha256 NOT GLOB '*[^0-9a-f]*'
                                )
                            ),
    sample_approved_by      TEXT    REFERENCES reviewers(reviewer_id)
                                        ON DELETE RESTRICT,
    sample_approved_at      TEXT,
    qa_of_run_id            INTEGER REFERENCES screening_runs(run_id)
                                        ON DELETE RESTRICT,
    prompt_version          TEXT    REFERENCES prompts(prompt_version)
                                        ON DELETE RESTRICT,
    prompt_sha256           TEXT    CHECK (
                                prompt_sha256 IS NULL OR (
                                    length(prompt_sha256) = 64
                                    AND prompt_sha256 NOT GLOB '*[^0-9a-f]*'
                                )
                            ),
    prompt_author_id        TEXT    REFERENCES reviewers(reviewer_id)
                                        ON DELETE RESTRICT,
    model_provider          TEXT,
    model_id                TEXT,
    model_version           TEXT,
    embedding_model         TEXT,
    parameters_json         TEXT    NOT NULL DEFAULT '{}'
                                CHECK (json_type(parameters_json) = 'object'),
    notes                   TEXT,
    approved_by             TEXT    NOT NULL
                                REFERENCES reviewers(reviewer_id)
                                ON DELETE RESTRICT,
    approved_at             TEXT    NOT NULL,
    CHECK (genuine_count <= evidence_count),
    CHECK (screened_count <= evidence_count),
    CHECK (qa_required = max(30, (screened_count + 9) / 10)),
    CHECK (run_type <> 'full' OR screened_count = evidence_count),
    CHECK (run_type = 'full' OR screened_count = sample_size),
    CHECK (
        (run_type IN ('pilot', 'qa')
         AND sample_seed IS NOT NULL
         AND sample_size IS NOT NULL
         AND frame_sha256 IS NOT NULL
         AND selection_algorithm IS NOT NULL
         AND lexicon_sha256 IS NOT NULL
         AND sample_approved_by IS NOT NULL
         AND sample_approved_at IS NOT NULL)
        OR
        (run_type = 'full'
         AND sample_seed IS NULL
         AND sample_size IS NULL
         AND frame_sha256 IS NULL
         AND selection_algorithm IS NULL
         AND lexicon_sha256 IS NULL
         AND sample_approved_by IS NULL
         AND sample_approved_at IS NULL)
    ),
    CHECK (
        (run_type = 'qa' AND qa_of_run_id IS NOT NULL)
        OR (run_type <> 'qa' AND qa_of_run_id IS NULL)
    ),
    CHECK (
        (model_id IS NULL AND model_provider IS NULL
         AND model_version IS NULL AND prompt_version IS NULL)
        OR
        (model_id IS NOT NULL AND model_provider IS NOT NULL
         AND model_version IS NOT NULL AND prompt_version IS NOT NULL)
    ),
    CHECK (
        (prompt_version IS NULL AND prompt_sha256 IS NULL)
        OR (prompt_version IS NOT NULL AND prompt_sha256 IS NOT NULL)
    ),
    CHECK (prompt_version IS NULL OR prompt_author_id IS NOT NULL)
);

CREATE TABLE screening_samples (
    sample_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL
                         REFERENCES screening_runs(run_id) ON DELETE RESTRICT,
    purpose          TEXT    NOT NULL CHECK (purpose IN ('pilot', 'qa')),
    evidence_id      TEXT    NOT NULL
                         REFERENCES evidence(evidence_id) ON DELETE RESTRICT,
    stratum          TEXT    NOT NULL CHECK (length(trim(stratum)) > 0),
    mandatory        INTEGER NOT NULL CHECK (mandatory IN (0, 1)),
    selection_rank   INTEGER NOT NULL CHECK (selection_rank > 0),
    selection_reason TEXT    NOT NULL
                         CHECK (length(trim(selection_reason)) > 0),
    created_at       TEXT    NOT NULL,
    UNIQUE (run_id, purpose, evidence_id),
    UNIQUE (run_id, purpose, selection_rank)
);

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
    directness               TEXT    CHECK (
                                 directness IS NULL
                                 OR directness IN ('direct', 'indirect')
                             ),
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
         AND directness IS NULL
         AND behaviour IS NULL
         AND first_time_status = 'not_established'
         AND sip_tenure = 'not_established')
        OR
        (relevance IN ('relevant', 'indirectly_relevant')
         AND directness IS NOT NULL
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
    field                   TEXT    NOT NULL CHECK (field IN (
                                'relevance', 'directness', 'behaviour',
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
    blind_to_other_reviewers INTEGER NOT NULL DEFAULT 1
                                CHECK (blind_to_other_reviewers IN (0, 1)),
    blind_to_model_draft    INTEGER NOT NULL DEFAULT 1
                                CHECK (blind_to_model_draft IN (0, 1)),
    independent_of_prompt_author INTEGER NOT NULL DEFAULT 1
                                CHECK (independent_of_prompt_author IN (0, 1)),
    comment                 TEXT,
    created_at              TEXT    NOT NULL,
    UNIQUE (run_id, evidence_id, reviewer_id, field),
    CHECK (
        (quote_text IS NULL AND quote_start IS NULL
         AND quote_end IS NULL AND quote_hash IS NULL)
        OR
        (quote_text IS NOT NULL AND quote_start IS NOT NULL
         AND quote_end IS NOT NULL AND quote_end > quote_start
         AND quote_hash IS NOT NULL)
    )
);

CREATE TABLE screening_reviews (
    review_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    screening_id             INTEGER NOT NULL
                                 REFERENCES screening_results(screening_id)
                                 ON DELETE RESTRICT,
    reviewer_id              TEXT    NOT NULL
                                 REFERENCES reviewers(reviewer_id)
                                 ON DELETE RESTRICT,
    reviewer_type            TEXT    NOT NULL CHECK (reviewer_type = 'human'),
    decision                 TEXT    NOT NULL CHECK (decision IN (
                                 'confirmed', 'changed', 'rejected', 'escalated'
                             )),
    reviewer_note            TEXT    NOT NULL
                                 CHECK (length(trim(reviewer_note)) > 0),
    replacement_screening_id INTEGER
                                 REFERENCES screening_results(screening_id)
                                 ON DELETE RESTRICT,
    blind_to_model_draft     INTEGER NOT NULL DEFAULT 1
                                 CHECK (blind_to_model_draft IN (0, 1)),
    independent_of_prompt_author INTEGER NOT NULL DEFAULT 1
                                 CHECK (independent_of_prompt_author IN (0, 1)),
    created_at               TEXT    NOT NULL,
    CHECK (
        (decision IN ('confirmed', 'changed', 'rejected')
         AND replacement_screening_id IS NOT NULL)
        OR
        (decision = 'escalated'
         AND replacement_screening_id IS NULL)
    ),
    CHECK (replacement_screening_id IS NULL
        OR replacement_screening_id <> screening_id)
);

CREATE TABLE screening_model_outputs (
    output_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL
                            REFERENCES screening_runs(run_id)
                            ON DELETE RESTRICT,
    evidence_id         TEXT    NOT NULL
                            REFERENCES evidence(evidence_id)
                            ON DELETE RESTRICT,
    attempt_no          INTEGER NOT NULL CHECK (attempt_no > 0),
    prompt_version      TEXT    NOT NULL
                            REFERENCES prompts(prompt_version)
                            ON DELETE RESTRICT,
    model_id            TEXT    NOT NULL
                            CHECK (length(trim(model_id)) > 0),
    raw_output          TEXT    NOT NULL
                            CHECK (length(raw_output) > 0),
    raw_output_sha256   TEXT    NOT NULL CHECK (
                            length(raw_output_sha256) = 64
                            AND raw_output_sha256 NOT GLOB '*[^0-9a-f]*'
                        ),
    parse_status        TEXT    NOT NULL CHECK (parse_status IN (
                            'valid', 'invalid_json', 'schema_invalid', 'rejected'
                        )),
    parse_error         TEXT,
    created_at          TEXT    NOT NULL,
    UNIQUE (run_id, evidence_id, attempt_no)
);

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
       directness,
       validation_status,
       COUNT(*) AS n
FROM v_run_screening
GROUP BY run_id, relevance, directness, validation_status;

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

CREATE INDEX idx_screening_runs_type
    ON screening_runs (run_type, qa_of_run_id);

CREATE INDEX idx_screening_runs_qa_of
    ON screening_runs (qa_of_run_id);

CREATE INDEX idx_screening_samples_evidence
    ON screening_samples (evidence_id);

CREATE INDEX idx_screening_model_outputs_run
    ON screening_model_outputs (run_id);

-- A record has exactly one un-superseded root across all runs.
CREATE UNIQUE INDEX ux_screening_results_one_root
    ON screening_results (evidence_id)
    WHERE supersedes_screening_id IS NULL;

-- A row can be superseded at most once.
CREATE UNIQUE INDEX ux_screening_results_superseded
    ON screening_results (supersedes_screening_id)
    WHERE supersedes_screening_id IS NOT NULL;

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

CREATE INDEX idx_screening_annotations_evidence
    ON screening_annotations (evidence_id);
CREATE INDEX idx_screening_annotations_reviewer
    ON screening_annotations (reviewer_id);

CREATE INDEX idx_screening_reviews_screening
    ON screening_reviews (screening_id);
CREATE INDEX idx_screening_reviews_replacement
    ON screening_reviews (replacement_screening_id);
CREATE INDEX idx_screening_reviews_reviewer
    ON screening_reviews (reviewer_id);

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

CREATE TRIGGER trg_reviewers_identity_immutable
BEFORE UPDATE ON reviewers
WHEN NEW.reviewer_id IS NOT OLD.reviewer_id
  OR NEW.display_name IS NOT OLD.display_name
  OR NEW.role IS NOT OLD.role
  OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'reviewer identity is immutable; only active may change');
END;

CREATE TRIGGER trg_reviewers_no_delete
BEFORE DELETE ON reviewers
BEGIN
    SELECT RAISE(ABORT, 'reviewers are never deleted; deactivate instead');
END;

CREATE TRIGGER trg_prompts_no_update
BEFORE UPDATE ON prompts
BEGIN
    SELECT RAISE(ABORT, 'prompts are append-only');
END;

CREATE TRIGGER trg_prompts_no_delete
BEFORE DELETE ON prompts
BEGIN
    SELECT RAISE(ABORT, 'prompts are append-only');
END;

CREATE TRIGGER trg_screening_runs_no_update
BEFORE UPDATE ON screening_runs
BEGIN
    SELECT RAISE(ABORT, 'screening_runs are append-only');
END;

CREATE TRIGGER trg_screening_runs_no_delete
BEFORE DELETE ON screening_runs
BEGIN
    SELECT RAISE(ABORT, 'screening_runs are append-only');
END;

CREATE TRIGGER trg_screening_samples_no_update
BEFORE UPDATE ON screening_samples
BEGIN
    SELECT RAISE(ABORT, 'screening_samples are append-only');
END;

CREATE TRIGGER trg_screening_samples_no_delete
BEFORE DELETE ON screening_samples
BEGIN
    SELECT RAISE(ABORT, 'screening_samples are append-only');
END;

CREATE TRIGGER trg_screening_model_outputs_no_update
BEFORE UPDATE ON screening_model_outputs
BEGIN
    SELECT RAISE(ABORT, 'screening_model_outputs are append-only');
END;

CREATE TRIGGER trg_screening_model_outputs_no_delete
BEFORE DELETE ON screening_model_outputs
BEGIN
    SELECT RAISE(ABORT, 'screening_model_outputs are append-only');
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

CREATE TRIGGER trg_screening_reviews_no_update
BEFORE UPDATE ON screening_reviews
BEGIN
    SELECT RAISE(ABORT, 'screening_reviews are append-only');
END;

CREATE TRIGGER trg_screening_reviews_no_delete
BEFORE DELETE ON screening_reviews
BEGIN
    SELECT RAISE(ABORT, 'screening_reviews are append-only');
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

CREATE TRIGGER trg_screening_runs_manifest_guard
BEFORE INSERT ON screening_runs
BEGIN
    SELECT RAISE(ABORT, 'corpus seal counts do not match the sealed evidence')
    WHERE NEW.evidence_count <> (
              SELECT COUNT(*) FROM evidence
              WHERE row_id <= NEW.corpus_seal_row_id)
       OR NEW.genuine_count <> (
              SELECT COUNT(*) FROM v_genuine_evidence
              WHERE row_id <= NEW.corpus_seal_row_id);

    SELECT RAISE(ABORT, 'approver must be an active product owner')
    WHERE NOT EXISTS (
        SELECT 1 FROM reviewers
        WHERE reviewer_id = NEW.approved_by
          AND role = 'product_owner'
          AND active = 1);

    SELECT RAISE(ABORT, 'sample approver must be an active product owner or adjudicator')
    WHERE NEW.sample_approved_by IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM reviewers
          WHERE reviewer_id = NEW.sample_approved_by
            AND role IN ('product_owner', 'adjudicator')
            AND active = 1);
END;

CREATE TRIGGER trg_screening_runs_qa_target
BEFORE INSERT ON screening_runs
WHEN NEW.run_type = 'qa'
BEGIN
    SELECT RAISE(ABORT, 'QA run must reference a compatible full run')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_runs f
        WHERE f.run_id = NEW.qa_of_run_id
          AND f.run_type = 'full'
          AND f.corpus_seal_row_id = NEW.corpus_seal_row_id
          AND f.corpus_digest_sha256 = NEW.corpus_digest_sha256
          AND f.evidence_count = NEW.evidence_count
          AND f.genuine_count = NEW.genuine_count
          AND f.label_version = NEW.label_version);
END;

CREATE TRIGGER trg_screening_samples_guard
BEFORE INSERT ON screening_samples
BEGIN
    SELECT RAISE(ABORT, 'sample purpose does not match run type')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_runs r
        WHERE r.run_id = NEW.run_id
          AND ((r.run_type = 'pilot' AND NEW.purpose = 'pilot')
            OR (r.run_type = 'qa'    AND NEW.purpose = 'qa')));

    SELECT RAISE(ABORT, 'sample evidence is outside the sealed corpus')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        JOIN screening_runs r ON r.run_id = NEW.run_id
        WHERE e.evidence_id = NEW.evidence_id
          AND e.row_id <= r.corpus_seal_row_id);

    SELECT RAISE(ABORT, 'QA sample must come from the referenced full run')
    WHERE NEW.purpose = 'qa'
      AND NOT EXISTS (
          SELECT 1
          FROM screening_runs q
          JOIN screening_results r
            ON r.run_id = q.qa_of_run_id
           AND r.evidence_id = NEW.evidence_id
          WHERE q.run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'sample exceeds the declared sample_size')
    WHERE (SELECT COUNT(*) FROM screening_samples s
           WHERE s.run_id = NEW.run_id AND s.purpose = NEW.purpose)
          >= (SELECT sample_size FROM screening_runs WHERE run_id = NEW.run_id);
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

CREATE TRIGGER trg_screening_annotations_guard
BEFORE INSERT ON screening_annotations
BEGIN
    SELECT RAISE(ABORT, 'annotations are only allowed for pilot or QA runs')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_runs
        WHERE run_id = NEW.run_id AND run_type IN ('pilot', 'qa'));

    SELECT RAISE(ABORT, 'annotation evidence is outside the sealed corpus')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        JOIN screening_runs r ON r.run_id = NEW.run_id
        WHERE e.evidence_id = NEW.evidence_id
          AND e.row_id <= r.corpus_seal_row_id);

    SELECT RAISE(ABORT, 'annotation reviewer is not an active reviewer or adjudicator')
    WHERE NOT EXISTS (
        SELECT 1 FROM reviewers
        WHERE reviewer_id = NEW.reviewer_id
          AND role IN ('reviewer', 'adjudicator')
          AND active = 1);

    SELECT RAISE(ABORT, 'annotation value is outside the vocabulary for its field')
    WHERE NOT (
        (NEW.field = 'relevance' AND NEW.value IN (
            'relevant', 'indirectly_relevant',
            'unrelated', 'insufficient_information'))
        OR (NEW.field = 'directness' AND NEW.value IN ('direct', 'indirect'))
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

CREATE TRIGGER trg_screening_reviews_guard
BEFORE INSERT ON screening_reviews
BEGIN
    SELECT RAISE(ABORT, 'reviewer is not an active reviewer, adjudicator or product owner')
    WHERE NOT EXISTS (
        SELECT 1 FROM reviewers
        WHERE reviewer_id = NEW.reviewer_id
          AND role IN ('reviewer', 'adjudicator', 'product_owner')
          AND active = 1);

    SELECT RAISE(ABORT, 'reviewer cannot review their own human-authored result')
    WHERE EXISTS (
        SELECT 1 FROM screening_results r
        WHERE r.screening_id = NEW.screening_id
          AND r.created_by_type = 'human'
          AND r.created_by = NEW.reviewer_id);

    SELECT RAISE(ABORT, 'reviewed result is already superseded by a different row')
    WHERE EXISTS (
        SELECT 1 FROM screening_results c
        WHERE c.supersedes_screening_id = NEW.screening_id
          AND c.screening_id IS NOT NEW.replacement_screening_id);

    SELECT RAISE(ABORT, 'run is already closed')
    WHERE EXISTS (
        SELECT 1 FROM screening_events
        WHERE run_id = (SELECT run_id FROM screening_results
                        WHERE screening_id = NEW.screening_id)
          AND event_type IN ('run_completed', 'run_aborted'));

    SELECT RAISE(ABORT, 'replacement must supersede the reviewed row for the same evidence')
    WHERE NEW.replacement_screening_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM screening_results rep
          JOIN screening_results old
            ON old.screening_id = NEW.screening_id
          WHERE rep.screening_id = NEW.replacement_screening_id
            AND rep.supersedes_screening_id = old.screening_id
            AND rep.evidence_id = old.evidence_id);

    SELECT RAISE(ABORT, 'replacement validation status does not match the decision')
    WHERE NEW.replacement_screening_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM screening_results rep
          WHERE rep.screening_id = NEW.replacement_screening_id
            AND NOT (
                (NEW.decision IN ('confirmed', 'changed')
                 AND rep.validation_status = 'human_validated')
                OR
                (NEW.decision = 'rejected'
                 AND rep.validation_status = 'rejected')
            ));

    SELECT RAISE(ABORT, 'reviewer cannot claim independence from their own prompt')
    WHERE NEW.independent_of_prompt_author = 1
      AND NEW.reviewer_id = (
          SELECT r.prompt_author_id
          FROM screening_runs r
          WHERE r.run_id = (
              SELECT s.run_id FROM screening_results s
              WHERE s.screening_id = NEW.screening_id
          ));
END;

CREATE TRIGGER trg_screening_model_outputs_guard
BEFORE INSERT ON screening_model_outputs
BEGIN
    SELECT RAISE(ABORT, 'model output evidence is outside the sealed corpus')
    WHERE NOT EXISTS (
        SELECT 1 FROM evidence e
        JOIN screening_runs r ON r.run_id = NEW.run_id
        WHERE e.evidence_id = NEW.evidence_id
          AND e.row_id <= r.corpus_seal_row_id);

    SELECT RAISE(ABORT, 'model output requires a model-assisted run')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_runs r
        WHERE r.run_id = NEW.run_id AND r.model_id IS NOT NULL);
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
END;

CREATE TRIGGER trg_evidence_legacy_classification_frozen
BEFORE UPDATE OF relevance, behaviour, directness, confidence ON evidence
WHEN NEW.relevance IS NOT OLD.relevance
  OR NEW.behaviour IS NOT OLD.behaviour
  OR NEW.directness IS NOT OLD.directness
  OR NEW.confidence IS NOT OLD.confidence
BEGIN
    SELECT RAISE(ABORT,
        'evidence classification fields are frozen; write screening_results instead');
END;
"""

# Object inventory used by the migration tests. Keep in sync with the SQL.
M3_TABLES: tuple[str, ...] = (
    "prompts",
    "reviewers",
    "screening_annotations",
    "screening_events",
    "screening_model_outputs",
    "screening_results",
    "screening_reviews",
    "screening_runs",
    "screening_samples",
)

M3_VIEWS: tuple[str, ...] = (
    "v_current_screening",
    "v_qa_coverage",
    "v_review_queue",
    "v_run_duplicate_counts",
    "v_run_screening",
    "v_screening_coverage",
    "v_screening_evidence",
)

M3_INDEXES: tuple[str, ...] = (
    "idx_screening_annotations_evidence",
    "idx_screening_annotations_reviewer",
    "idx_screening_events_created",
    "idx_screening_events_result",
    "idx_screening_events_run",
    "idx_screening_model_outputs_run",
    "idx_screening_results_evidence",
    "idx_screening_results_review_priority",
    "idx_screening_results_run",
    "idx_screening_results_run_validation",
    "idx_screening_results_validation",
    "idx_screening_reviews_replacement",
    "idx_screening_reviews_reviewer",
    "idx_screening_reviews_screening",
    "idx_screening_runs_qa_of",
    "idx_screening_runs_type",
    "idx_screening_samples_evidence",
    "ux_screening_events_created",
    "ux_screening_events_terminal",
    "ux_screening_results_one_root",
    "ux_screening_results_superseded",
)

M3_TRIGGERS: tuple[str, ...] = (
    "trg_evidence_legacy_classification_frozen",
    "trg_prompts_no_delete",
    "trg_prompts_no_update",
    "trg_reviewers_identity_immutable",
    "trg_reviewers_no_delete",
    "trg_screening_annotations_guard",
    "trg_screening_annotations_no_delete",
    "trg_screening_annotations_no_update",
    "trg_screening_annotations_quote_guard",
    "trg_screening_events_completion_guard",
    "trg_screening_events_lifecycle_guard",
    "trg_screening_events_no_delete",
    "trg_screening_events_no_update",
    "trg_screening_events_result_run_guard",
    "trg_screening_model_outputs_guard",
    "trg_screening_model_outputs_no_delete",
    "trg_screening_model_outputs_no_update",
    "trg_screening_results_no_delete",
    "trg_screening_results_no_update",
    "trg_screening_results_quote_guard",
    "trg_screening_results_run_guard",
    "trg_screening_results_secondary_behaviours",
    "trg_screening_results_supersede_guard",
    "trg_screening_reviews_guard",
    "trg_screening_reviews_no_delete",
    "trg_screening_reviews_no_update",
    "trg_screening_runs_manifest_guard",
    "trg_screening_runs_no_delete",
    "trg_screening_runs_no_update",
    "trg_screening_runs_qa_target",
    "trg_screening_samples_guard",
    "trg_screening_samples_no_delete",
    "trg_screening_samples_no_update",
)

M3_LEGACY_CLASSIFICATION_COLUMNS: tuple[str, ...] = (
    "relevance",
    "behaviour",
    "directness",
    "confidence",
)

__all__ = [
    "M3_SCREENING_STORE_SQL",
    "M3_TABLES",
    "M3_VIEWS",
    "M3_INDEXES",
    "M3_TRIGGERS",
    "M3_LEGACY_CLASSIFICATION_COLUMNS",
]
