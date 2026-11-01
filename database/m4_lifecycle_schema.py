"""Migration v6: run lifecycle, freeze, Pass 2 order and AI provenance.

Additive v5→v6 migration. It does not rebuild existing tables. It:

* adds ``pass2_order_sha256`` to annotations;
* adds authoritative freeze, Pass 2 order/import, AI draft/suggestion and
  annotation-provenance tables;
* rebuilds only the annotation guards and adds append-only guards for new tables;
* aborts on incompatible historical Pass 2 data instead of inventing history.
"""

from __future__ import annotations

M6_LIFECYCLE_SQL = r"""
CREATE TABLE _m6_guard_preconditions (
    n INTEGER NOT NULL CHECK (n = 0)
);
INSERT INTO _m6_guard_preconditions (n)
SELECT 1
WHERE NOT EXISTS (
          SELECT 1 FROM schema_migrations WHERE version = 5)
   OR EXISTS (
          SELECT 1 FROM schema_migrations WHERE version = 6)
   OR NOT EXISTS (
          SELECT 1 FROM sqlite_master
          WHERE type = 'table' AND name = 'screening_annotations')
   OR EXISTS (
          SELECT 1 FROM pragma_table_info('screening_annotations')
          WHERE name = 'pass2_order_sha256')
   OR EXISTS (
          SELECT 1 FROM screening_annotations WHERE pass_no = 2)
   OR EXISTS (
          SELECT 1 FROM sqlite_master
          WHERE type = 'table' AND name IN (
              'screening_pass1_freezes', 'screening_pass2_orders',
              'screening_pass2_imports', 'screening_ai_drafts',
              'screening_ai_suggestions', 'screening_annotation_provenance'))
   OR EXISTS (
          SELECT 1 FROM screening_events
          WHERE event_type = 'run_started'
          GROUP BY run_id HAVING COUNT(*) > 1);
DROP TABLE _m6_guard_preconditions;

ALTER TABLE screening_annotations ADD COLUMN pass2_order_sha256 TEXT;

CREATE TABLE screening_pass1_freezes (
    run_id              INTEGER PRIMARY KEY
                            REFERENCES screening_runs(run_id)
                            ON DELETE RESTRICT,
    annotation_count    INTEGER NOT NULL CHECK (annotation_count > 0),
    record_count        INTEGER NOT NULL CHECK (record_count > 0),
    last_recorded_at    TEXT    NOT NULL,
    sha256              TEXT    NOT NULL CHECK (
                            length(sha256) = 64
                            AND sha256 NOT GLOB '*[^0-9a-f]*'
                        ),
    sealed_path         TEXT    NOT NULL CHECK (length(trim(sealed_path)) > 0),
    frozen_at           TEXT    NOT NULL,
    frozen_by           TEXT    NOT NULL
                            REFERENCES reviewers(reviewer_id)
                            ON DELETE RESTRICT,
    event_id            INTEGER NOT NULL UNIQUE
                            REFERENCES screening_events(event_id)
                            ON DELETE RESTRICT
);

CREATE TABLE screening_pass2_orders (
    run_id              INTEGER PRIMARY KEY
                            REFERENCES screening_runs(run_id)
                            ON DELETE RESTRICT,
    seed                INTEGER NOT NULL,
    row_count           INTEGER NOT NULL CHECK (row_count > 0),
    pass1_order_sha256  TEXT    NOT NULL CHECK (
                            length(pass1_order_sha256) = 64
                            AND pass1_order_sha256 NOT GLOB '*[^0-9a-f]*'
                        ),
    pass2_order_sha256  TEXT    NOT NULL CHECK (
                            length(pass2_order_sha256) = 64
                            AND pass2_order_sha256 NOT GLOB '*[^0-9a-f]*'
                        ),
    file_sha256         TEXT    NOT NULL CHECK (
                            length(file_sha256) = 64
                            AND file_sha256 NOT GLOB '*[^0-9a-f]*'
                        ),
    file_path           TEXT    NOT NULL CHECK (length(trim(file_path)) > 0),
    generated_at        TEXT    NOT NULL,
    generated_by        TEXT    NOT NULL
                            REFERENCES reviewers(reviewer_id)
                            ON DELETE RESTRICT,
    event_id            INTEGER NOT NULL UNIQUE
                            REFERENCES screening_events(event_id)
                            ON DELETE RESTRICT
);

CREATE TABLE screening_pass2_imports (
    run_id              INTEGER PRIMARY KEY
                            REFERENCES screening_runs(run_id)
                            ON DELETE RESTRICT,
    order_sha256        TEXT    NOT NULL CHECK (
                            length(order_sha256) = 64
                            AND order_sha256 NOT GLOB '*[^0-9a-f]*'
                        ),
    file_sha256         TEXT    NOT NULL CHECK (
                            length(file_sha256) = 64
                            AND file_sha256 NOT GLOB '*[^0-9a-f]*'
                        ),
    annotation_count    INTEGER NOT NULL CHECK (annotation_count > 0),
    imported_at         TEXT    NOT NULL,
    imported_by         TEXT    NOT NULL
                            REFERENCES reviewers(reviewer_id)
                            ON DELETE RESTRICT
);

CREATE TABLE screening_ai_drafts (
    draft_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              INTEGER NOT NULL
                            REFERENCES screening_runs(run_id)
                            ON DELETE RESTRICT,
    source_system       TEXT    NOT NULL CHECK (length(trim(source_system)) > 0),
    source_version      TEXT    NOT NULL CHECK (length(trim(source_version)) > 0),
    source_file         TEXT    NOT NULL CHECK (length(trim(source_file)) > 0),
    file_sha256         TEXT    NOT NULL CHECK (
                            length(file_sha256) = 64
                            AND file_sha256 NOT GLOB '*[^0-9a-f]*'
                        ),
    record_count        INTEGER NOT NULL CHECK (record_count > 0),
    label_version       TEXT    NOT NULL,
    generated_at        TEXT    NOT NULL,
    registered_at       TEXT    NOT NULL,
    registered_by       TEXT    NOT NULL
                            REFERENCES reviewers(reviewer_id)
                            ON DELETE RESTRICT,
    status              TEXT    NOT NULL DEFAULT 'registered'
                            CHECK (status IN ('registered', 'superseded')),
    UNIQUE (run_id, file_sha256)
);

CREATE TABLE screening_ai_suggestions (
    suggestion_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id            INTEGER NOT NULL
                            REFERENCES screening_ai_drafts(draft_id)
                            ON DELETE RESTRICT,
    evidence_id         TEXT    NOT NULL
                            REFERENCES evidence(evidence_id)
                            ON DELETE RESTRICT,
    field               TEXT    NOT NULL CHECK (field IN (
                            'relevance', 'behaviour',
                            'first_time_status', 'sip_tenure'
                        )),
    value               TEXT    NOT NULL CHECK (length(trim(value)) > 0),
    quote_text          TEXT,
    quote_start         INTEGER CHECK (quote_start IS NULL OR quote_start >= 0),
    quote_end           INTEGER CHECK (quote_end IS NULL OR quote_end >= 0),
    quote_hash          TEXT    CHECK (
                            quote_hash IS NULL OR (
                                length(quote_hash) = 64
                                AND quote_hash NOT GLOB '*[^0-9a-f]*'
                            )
                        ),
    comment             TEXT,
    created_at          TEXT    NOT NULL,
    UNIQUE (draft_id, evidence_id, field),
    CHECK (
        (quote_text IS NULL AND quote_start IS NULL
         AND quote_end IS NULL AND quote_hash IS NULL)
        OR
        (quote_text IS NOT NULL AND quote_start IS NOT NULL
         AND quote_end IS NOT NULL AND quote_end > quote_start
         AND quote_hash IS NOT NULL)
    )
);

CREATE TABLE screening_annotation_provenance (
    annotation_id       INTEGER PRIMARY KEY
                            REFERENCES screening_annotations(annotation_id)
                            ON DELETE RESTRICT,
    run_id              INTEGER NOT NULL
                            REFERENCES screening_runs(run_id)
                            ON DELETE RESTRICT,
    evidence_id         TEXT    NOT NULL
                            REFERENCES evidence(evidence_id)
                            ON DELETE RESTRICT,
    reviewer_id         TEXT    NOT NULL
                            REFERENCES reviewers(reviewer_id)
                            ON DELETE RESTRICT,
    pass_no             INTEGER NOT NULL CHECK (pass_no = 1),
    field               TEXT    NOT NULL CHECK (field IN (
                            'relevance', 'behaviour',
                            'first_time_status', 'sip_tenure'
                        )),
    ai_draft_id         INTEGER REFERENCES screening_ai_drafts(draft_id)
                            ON DELETE RESTRICT,
    ai_suggestion_id    INTEGER REFERENCES screening_ai_suggestions(suggestion_id)
                            ON DELETE RESTRICT,
    human_decision      TEXT    NOT NULL CHECK (human_decision IN (
                            'confirmed', 'corrected', 'independent'
                        )),
    ai_value            TEXT,
    ai_quote_hash       TEXT,
    ai_comment_sha256   TEXT,
    value_origin        TEXT    NOT NULL CHECK (value_origin IN (
                            'ai_confirmed', 'human_edited', 'human_new'
                        )),
    quote_origin        TEXT    NOT NULL CHECK (quote_origin IN (
                            'ai_confirmed', 'human_edited', 'human_new'
                        )),
    comment_origin      TEXT    NOT NULL CHECK (comment_origin IN (
                            'ai_confirmed', 'human_edited', 'human_new'
                        )),
    verified_by         TEXT    NOT NULL
                            REFERENCES reviewers(reviewer_id)
                            ON DELETE RESTRICT,
    verified_at         TEXT    NOT NULL,
    CHECK (
        (human_decision = 'independent'
         AND ai_draft_id IS NULL AND ai_suggestion_id IS NULL
         AND ai_value IS NULL AND ai_quote_hash IS NULL
         AND ai_comment_sha256 IS NULL)
        OR
        (human_decision IN ('confirmed', 'corrected')
         AND ai_draft_id IS NOT NULL AND ai_suggestion_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX ux_screening_events_started
    ON screening_events (run_id)
    WHERE event_type = 'run_started';

CREATE UNIQUE INDEX ux_screening_events_signoff
    ON screening_events (run_id)
    WHERE event_type = 'review_completed';

CREATE INDEX idx_screening_ai_suggestions_draft
    ON screening_ai_suggestions (draft_id, evidence_id);
CREATE INDEX idx_screening_ai_suggestions_evidence
    ON screening_ai_suggestions (evidence_id);
CREATE INDEX idx_screening_annotation_provenance_run
    ON screening_annotation_provenance (run_id, pass_no);
CREATE INDEX idx_screening_pass2_orders_created
    ON screening_pass2_orders (generated_at);
CREATE INDEX idx_screening_pass1_freezes_frozen
    ON screening_pass1_freezes (frozen_at);

CREATE VIEW v_run_state AS
SELECT r.run_id,
       CASE
           WHEN EXISTS (SELECT 1 FROM screening_events e
                        WHERE e.run_id = r.run_id AND e.event_type = 'run_completed')
               THEN 'completed'
           WHEN EXISTS (SELECT 1 FROM screening_events e
                        WHERE e.run_id = r.run_id AND e.event_type = 'run_aborted')
               THEN 'aborted'
           WHEN EXISTS (SELECT 1 FROM screening_events e
                        WHERE e.run_id = r.run_id AND e.event_type = 'review_completed')
               THEN 'signed_off'
           WHEN EXISTS (SELECT 1 FROM screening_pass2_orders o
                        WHERE o.run_id = r.run_id)
               THEN 'open_pass2'
           WHEN EXISTS (SELECT 1 FROM screening_pass1_freezes f
                        WHERE f.run_id = r.run_id)
               THEN 'pass1_frozen'
           WHEN EXISTS (SELECT 1 FROM screening_events e
                        WHERE e.run_id = r.run_id AND e.event_type = 'run_started')
               THEN 'open_pass1'
           ELSE 'created'
       END AS state,
       (SELECT MAX(e.created_at) FROM screening_events e
        WHERE e.run_id = r.run_id AND e.event_type = 'run_started') AS started_at,
       (SELECT f.frozen_at FROM screening_pass1_freezes f
        WHERE f.run_id = r.run_id) AS pass1_frozen_at,
       (SELECT o.generated_at FROM screening_pass2_orders o
        WHERE o.run_id = r.run_id) AS pass2_generated_at,
       (SELECT MAX(e.created_at) FROM screening_events e
        WHERE e.run_id = r.run_id AND e.event_type = 'review_completed') AS signed_off_at,
       (SELECT MAX(e.created_at) FROM screening_events e
        WHERE e.run_id = r.run_id AND e.event_type = 'run_completed') AS completed_at
FROM screening_runs r;

CREATE VIEW v_ai_draft_summary AS
SELECT d.draft_id,
       d.run_id,
       d.source_system,
       d.source_version,
       d.file_sha256,
       d.record_count,
       COUNT(s.suggestion_id) AS suggestion_count,
       SUM(CASE WHEN s.field = 'relevance' THEN 1 ELSE 0 END) AS relevance_count
FROM screening_ai_drafts d
LEFT JOIN screening_ai_suggestions s ON s.draft_id = d.draft_id
GROUP BY d.draft_id;

CREATE VIEW v_annotation_provenance_summary AS
SELECT p.run_id,
       p.pass_no,
       p.human_decision,
       COUNT(*) AS n,
       SUM(CASE WHEN p.value_origin = 'ai_confirmed' THEN 1 ELSE 0 END) AS ai_confirmed_values,
       SUM(CASE WHEN p.value_origin = 'human_edited' THEN 1 ELSE 0 END) AS human_edited_values,
       SUM(CASE WHEN p.value_origin = 'human_new' THEN 1 ELSE 0 END) AS human_new_values
FROM screening_annotation_provenance p
GROUP BY p.run_id, p.pass_no, p.human_decision;

DROP TRIGGER trg_screening_annotations_guard;
DROP TRIGGER trg_screening_annotations_pass_guard;
DROP TRIGGER trg_screening_events_lifecycle_guard;

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

    SELECT RAISE(ABORT, 'run is already started')
    WHERE NEW.event_type = 'run_started'
      AND EXISTS (
          SELECT 1 FROM screening_events
          WHERE run_id = NEW.run_id AND event_type = 'run_started');

    SELECT RAISE(ABORT, 'run cannot start after freeze, Pass 2 or completion')
    WHERE NEW.event_type = 'run_started'
      AND (
          EXISTS (SELECT 1 FROM screening_pass1_freezes
                  WHERE run_id = NEW.run_id)
          OR EXISTS (SELECT 1 FROM screening_pass2_orders
                     WHERE run_id = NEW.run_id)
          OR EXISTS (SELECT 1 FROM screening_events
                     WHERE run_id = NEW.run_id
                       AND event_type IN ('run_completed', 'run_aborted'))
          OR EXISTS (SELECT 1 FROM screening_annotations
                     WHERE run_id = NEW.run_id));

    SELECT RAISE(ABORT, 'run_started must be authored by the approved researcher')
    WHERE NEW.event_type = 'run_started'
      AND NEW.actor_type <> 'human';

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

    SELECT RAISE(ABORT, 'Pass 1 annotations are closed after freeze')
    WHERE NEW.pass_no = 1
      AND EXISTS (SELECT 1 FROM screening_pass1_freezes
                  WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'Pass 1 requires a null Pass 2 order hash')
    WHERE NEW.pass_no = 1 AND NEW.pass2_order_sha256 IS NOT NULL;

    SELECT RAISE(ABORT, 'Pass 2 requires a Pass 1 freeze')
    WHERE NEW.pass_no = 2
      AND NOT EXISTS (SELECT 1 FROM screening_pass1_freezes
                      WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'Pass 2 requires the approved generated order')
    WHERE NEW.pass_no = 2
      AND (
          NEW.pass2_order_sha256 IS NULL
          OR NOT EXISTS (
              SELECT 1 FROM screening_pass2_orders o
              WHERE o.run_id = NEW.run_id
                AND o.pass2_order_sha256 = NEW.pass2_order_sha256));
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

    SELECT RAISE(ABORT, 'Pass 2 requires a complete Pass 1')
    WHERE NEW.pass_no = 2
      AND (
          (SELECT COUNT(*) FROM screening_annotations a
           WHERE a.run_id = NEW.run_id AND a.pass_no = 1)
              <> (SELECT screened_count * 4 FROM screening_runs
                  WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(DISTINCT a.evidence_id) FROM screening_annotations a
           WHERE a.run_id = NEW.run_id AND a.pass_no = 1)
              <> (SELECT screened_count FROM screening_runs
                  WHERE run_id = NEW.run_id));

    SELECT RAISE(ABORT, 'Pass 2 requires at least 48 hours after the last Pass 1 annotation')
    WHERE NEW.pass_no = 2
      AND julianday(NEW.recorded_at) - julianday(
              (SELECT MAX(a.recorded_at) FROM screening_annotations a
               WHERE a.run_id = NEW.run_id AND a.pass_no = 1)) < 2.0;

    SELECT RAISE(ABORT, 'pass 2 must set blind_to_prior_pass = 1')
    WHERE NEW.pass_no = 2 AND NEW.blind_to_prior_pass <> 1;

    SELECT RAISE(ABORT, 'pass 1 has no prior pass and must set blind_to_prior_pass = 1')
    WHERE NEW.pass_no = 1 AND NEW.blind_to_prior_pass <> 1;
END;

CREATE TRIGGER trg_screening_pass1_freezes_guard
BEFORE INSERT ON screening_pass1_freezes
BEGIN
    SELECT RAISE(ABORT, 'freeze requires an existing started run')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_events
        WHERE run_id = NEW.run_id AND event_type = 'run_started');

    SELECT RAISE(ABORT, 'freeze is not allowed after Pass 2 or completion')
    WHERE EXISTS (SELECT 1 FROM screening_pass2_orders WHERE run_id = NEW.run_id)
       OR EXISTS (SELECT 1 FROM screening_annotations
                  WHERE run_id = NEW.run_id AND pass_no = 2)
       OR EXISTS (SELECT 1 FROM screening_events
                  WHERE run_id = NEW.run_id
                    AND event_type IN ('run_completed', 'run_aborted'));

    SELECT RAISE(ABORT, 'freeze already exists')
    WHERE EXISTS (SELECT 1 FROM screening_pass1_freezes WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'freeze count does not match the pilot sample')
    WHERE NEW.annotation_count <> (
              SELECT screened_count * 4 FROM screening_runs
              WHERE run_id = NEW.run_id)
       OR NEW.record_count <> (
              SELECT screened_count FROM screening_runs
              WHERE run_id = NEW.run_id)
       OR (SELECT COUNT(*) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 1) <> NEW.annotation_count
       OR (SELECT COUNT(DISTINCT evidence_id) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 1) <> NEW.record_count;

    SELECT RAISE(ABORT, 'freeze requires four fields for every sampled record')
    WHERE EXISTS (
        SELECT 1 FROM screening_samples s
        WHERE s.run_id = NEW.run_id AND s.purpose = 'pilot'
          AND (SELECT COUNT(*) FROM screening_annotations a
               WHERE a.run_id = s.run_id
                 AND a.evidence_id = s.evidence_id
                 AND a.pass_no = 1) <> 4);

    SELECT RAISE(ABORT, 'freeze requires complete human provenance')
    WHERE (SELECT COUNT(*) FROM screening_annotations a
           WHERE a.run_id = NEW.run_id AND a.pass_no = 1
             AND NOT EXISTS (
                 SELECT 1 FROM screening_annotation_provenance p
                 WHERE p.annotation_id = a.annotation_id))
          <> 0
       OR (SELECT COUNT(*) FROM screening_annotation_provenance
           WHERE run_id = NEW.run_id AND pass_no = 1) <> NEW.annotation_count;

    SELECT RAISE(ABORT, 'freeze timestamp does not match stored annotations')
    WHERE NEW.last_recorded_at IS NOT (
        SELECT MAX(recorded_at) FROM screening_annotations
        WHERE run_id = NEW.run_id AND pass_no = 1);

    SELECT RAISE(ABORT, 'freeze must reference a pass_1_frozen event')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_events e
        WHERE e.event_id = NEW.event_id
          AND e.run_id = NEW.run_id
          AND e.event_type = 'run_suspended'
          AND json_extract(e.detail_json, '$.reason') = 'pass_1_frozen');

    SELECT RAISE(ABORT, 'freeze actor must be the approved researcher')
    WHERE NEW.frozen_by IS NOT (
        SELECT approved_by FROM screening_runs WHERE run_id = NEW.run_id);
END;

CREATE TRIGGER trg_screening_pass2_orders_guard
BEFORE INSERT ON screening_pass2_orders
BEGIN
    SELECT RAISE(ABORT, 'Pass 2 order requires a Pass 1 freeze')
    WHERE NOT EXISTS (SELECT 1 FROM screening_pass1_freezes
                      WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'Pass 2 order already exists')
    WHERE EXISTS (SELECT 1 FROM screening_pass2_orders WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'Pass 2 order is not allowed after completion')
    WHERE EXISTS (SELECT 1 FROM screening_events
                  WHERE run_id = NEW.run_id
                    AND event_type IN ('run_completed', 'run_aborted'));

    SELECT RAISE(ABORT, 'Pass 2 order row count does not match the sample')
    WHERE NEW.row_count <> (
        SELECT screened_count FROM screening_runs WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'Pass 2 order must reference a run_resumed event')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_events e
        WHERE e.event_id = NEW.event_id
          AND e.run_id = NEW.run_id
          AND e.event_type = 'run_resumed');

    SELECT RAISE(ABORT, 'Pass 2 order actor must be the approved researcher')
    WHERE NEW.generated_by IS NOT (
        SELECT approved_by FROM screening_runs WHERE run_id = NEW.run_id);
END;

CREATE TRIGGER trg_screening_pass2_imports_guard
BEFORE INSERT ON screening_pass2_imports
BEGIN
    SELECT RAISE(ABORT, 'Pass 2 import requires the approved order')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_pass2_orders o
        WHERE o.run_id = NEW.run_id
          AND o.pass2_order_sha256 = NEW.order_sha256);

    SELECT RAISE(ABORT, 'Pass 2 import already exists')
    WHERE EXISTS (SELECT 1 FROM screening_pass2_imports WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'Pass 2 import is incomplete')
    WHERE NEW.annotation_count <> (
              SELECT screened_count * 4 FROM screening_runs
              WHERE run_id = NEW.run_id)
       OR (SELECT COUNT(*) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2) <> NEW.annotation_count
       OR (SELECT COUNT(DISTINCT evidence_id) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2) <> (
              SELECT screened_count FROM screening_runs WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'Pass 2 import requires the approved order hash on every row')
    WHERE EXISTS (
        SELECT 1 FROM screening_annotations
        WHERE run_id = NEW.run_id AND pass_no = 2
          AND pass2_order_sha256 IS NOT NEW.order_sha256);

    SELECT RAISE(ABORT, 'Pass 2 import violates the 48-hour gap')
    WHERE EXISTS (
        SELECT 1 FROM screening_annotations
        WHERE run_id = NEW.run_id AND pass_no = 2
          AND julianday(recorded_at) - julianday(
              (SELECT MAX(recorded_at) FROM screening_annotations
               WHERE run_id = NEW.run_id AND pass_no = 1)) < 2.0);

    SELECT RAISE(ABORT, 'Pass 2 import requires blind_to_prior_pass = 1')
    WHERE EXISTS (
        SELECT 1 FROM screening_annotations
        WHERE run_id = NEW.run_id AND pass_no = 2
          AND blind_to_prior_pass <> 1);
END;

CREATE TRIGGER trg_screening_ai_drafts_guard
BEFORE INSERT ON screening_ai_drafts
BEGIN
    SELECT RAISE(ABORT, 'AI draft record count does not match the pilot sample')
    WHERE NEW.record_count <> (
        SELECT screened_count FROM screening_runs WHERE run_id = NEW.run_id);

    SELECT RAISE(ABORT, 'AI draft must be registered by an active reviewer')
    WHERE NOT EXISTS (
        SELECT 1 FROM reviewers
        WHERE reviewer_id = NEW.registered_by AND active = 1);
END;

CREATE TRIGGER trg_screening_ai_suggestions_guard
BEFORE INSERT ON screening_ai_suggestions
BEGIN
    SELECT RAISE(ABORT, 'AI suggestion evidence is not in the draft run sample')
    WHERE NOT EXISTS (
        SELECT 1
        FROM screening_ai_drafts d
        JOIN screening_samples s
          ON s.run_id = d.run_id AND s.purpose = 'pilot'
        WHERE d.draft_id = NEW.draft_id
          AND s.evidence_id = NEW.evidence_id);

    SELECT RAISE(ABORT, 'AI suggestion value is outside the vocabulary')
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

CREATE TRIGGER trg_screening_annotation_provenance_guard
BEFORE INSERT ON screening_annotation_provenance
BEGIN
    SELECT RAISE(ABORT, 'provenance must reference a matching Pass 1 annotation')
    WHERE NOT EXISTS (
        SELECT 1 FROM screening_annotations a
        WHERE a.annotation_id = NEW.annotation_id
          AND a.run_id = NEW.run_id
          AND a.evidence_id = NEW.evidence_id
          AND a.reviewer_id = NEW.reviewer_id
          AND a.pass_no = 1
          AND a.field = NEW.field);

    SELECT RAISE(ABORT, 'confirmed/corrected provenance requires an AI suggestion')
    WHERE NEW.human_decision IN ('confirmed', 'corrected')
      AND (NEW.ai_suggestion_id IS NULL OR NEW.ai_draft_id IS NULL);

    SELECT RAISE(ABORT, 'independent provenance must not link an AI suggestion')
    WHERE NEW.human_decision = 'independent'
      AND (NEW.ai_suggestion_id IS NOT NULL OR NEW.ai_draft_id IS NOT NULL
           OR NEW.ai_value IS NOT NULL OR NEW.ai_quote_hash IS NOT NULL
           OR NEW.ai_comment_sha256 IS NOT NULL);

    SELECT RAISE(ABORT, 'AI provenance suggestion does not match the draft')
    WHERE NEW.ai_suggestion_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM screening_ai_suggestions s
          WHERE s.suggestion_id = NEW.ai_suggestion_id
            AND s.draft_id = NEW.ai_draft_id
            AND s.evidence_id = NEW.evidence_id
            AND s.field = NEW.field);

    SELECT RAISE(ABORT, 'confirmed provenance does not match the AI value')
    WHERE NEW.human_decision = 'confirmed'
      AND EXISTS (
          SELECT 1
          FROM screening_annotations a
          JOIN screening_ai_suggestions s ON s.suggestion_id = NEW.ai_suggestion_id
          WHERE a.annotation_id = NEW.annotation_id
            AND (a.value <> NEW.ai_value
                 OR a.quote_hash IS NOT NEW.ai_quote_hash));

    SELECT RAISE(ABORT, 'corrected provenance must differ from the AI suggestion')
    WHERE NEW.human_decision = 'corrected'
      AND NOT EXISTS (
          SELECT 1
          FROM screening_annotations a
          JOIN screening_ai_suggestions s ON s.suggestion_id = NEW.ai_suggestion_id
          WHERE a.annotation_id = NEW.annotation_id
            AND (a.value <> NEW.ai_value
                 OR a.quote_hash IS NOT NEW.ai_quote_hash));

    SELECT RAISE(ABORT, 'provenance verifier must be an active reviewer')
    WHERE NOT EXISTS (
        SELECT 1 FROM reviewers
        WHERE reviewer_id = NEW.verified_by AND active = 1);
END;

CREATE TRIGGER trg_screening_pass1_freezes_no_update
BEFORE UPDATE ON screening_pass1_freezes
BEGIN
    SELECT RAISE(ABORT, 'pass 1 freezes are immutable');
END;
CREATE TRIGGER trg_screening_pass1_freezes_no_delete
BEFORE DELETE ON screening_pass1_freezes
BEGIN
    SELECT RAISE(ABORT, 'pass 1 freezes are immutable');
END;
CREATE TRIGGER trg_screening_pass2_orders_no_update
BEFORE UPDATE ON screening_pass2_orders
BEGIN
    SELECT RAISE(ABORT, 'pass 2 orders are immutable');
END;
CREATE TRIGGER trg_screening_pass2_orders_no_delete
BEFORE DELETE ON screening_pass2_orders
BEGIN
    SELECT RAISE(ABORT, 'pass 2 orders are immutable');
END;
CREATE TRIGGER trg_screening_pass2_imports_no_update
BEFORE UPDATE ON screening_pass2_imports
BEGIN
    SELECT RAISE(ABORT, 'pass 2 imports are immutable');
END;
CREATE TRIGGER trg_screening_pass2_imports_no_delete
BEFORE DELETE ON screening_pass2_imports
BEGIN
    SELECT RAISE(ABORT, 'pass 2 imports are immutable');
END;
CREATE TRIGGER trg_screening_ai_drafts_no_update
BEFORE UPDATE ON screening_ai_drafts
BEGIN
    SELECT RAISE(ABORT, 'AI drafts are immutable');
END;
CREATE TRIGGER trg_screening_ai_drafts_no_delete
BEFORE DELETE ON screening_ai_drafts
BEGIN
    SELECT RAISE(ABORT, 'AI drafts are immutable');
END;
CREATE TRIGGER trg_screening_ai_suggestions_no_update
BEFORE UPDATE ON screening_ai_suggestions
BEGIN
    SELECT RAISE(ABORT, 'AI suggestions are immutable');
END;
CREATE TRIGGER trg_screening_ai_suggestions_no_delete
BEFORE DELETE ON screening_ai_suggestions
BEGIN
    SELECT RAISE(ABORT, 'AI suggestions are immutable');
END;
CREATE TRIGGER trg_screening_annotation_provenance_no_update
BEFORE UPDATE ON screening_annotation_provenance
BEGIN
    SELECT RAISE(ABORT, 'annotation provenance is immutable');
END;
CREATE TRIGGER trg_screening_annotation_provenance_no_delete
BEFORE DELETE ON screening_annotation_provenance
BEGIN
    SELECT RAISE(ABORT, 'annotation provenance is immutable');
END;
"""

M6_TABLES: tuple[str, ...] = (
    "screening_pass1_freezes",
    "screening_pass2_orders",
    "screening_pass2_imports",
    "screening_ai_drafts",
    "screening_ai_suggestions",
    "screening_annotation_provenance",
)

M6_VIEWS: tuple[str, ...] = (
    "v_run_state",
    "v_ai_draft_summary",
    "v_annotation_provenance_summary",
)

__all__ = ["M6_LIFECYCLE_SQL", "M6_TABLES", "M6_VIEWS"]
