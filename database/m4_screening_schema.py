"""Migration v4 schema: the M4 single-researcher two-pass screening store.

This module is the executable form of the approved v4 technical design in
``docs/M4_V4_MIGRATION_DESIGN.md`` sections 10.2-10.8. It is imported by
``database.migrations`` as the version-4 migration script.

The script is intentionally ordered:

1. the precondition guard (v3, no v4 objects, zero legacy annotations),
2. the run-mode column,
3. the empty annotation-table rebuild (no row copy),
4. the annotation indexes and four new audit views,
5. the affected run/review/completion guards,
6. the annotation, result, event and sign-off guards.

It is additive except for the empty annotation-table rebuild. It does not alter
``evidence`` and does not create reviewers, runs, samples or screening data.
"""

from __future__ import annotations

from database.screening_schema import M3_INDEXES, M3_TABLES, M3_TRIGGERS, M3_VIEWS

M4_SCREENING_STORE_SQL = r"""
CREATE TABLE _m4_guard_preconditions (
    n INTEGER NOT NULL CHECK (n = 0)
);
INSERT INTO _m4_guard_preconditions (n)
SELECT 1
WHERE NOT EXISTS (
          SELECT 1 FROM schema_migrations WHERE version = 3)
   OR EXISTS (
          SELECT 1 FROM pragma_table_info('screening_runs')
          WHERE name = 'review_mode')
   OR EXISTS (
          SELECT 1 FROM pragma_table_info('screening_annotations')
          WHERE name = 'pass_no')
   OR NOT EXISTS (
          SELECT 1 FROM sqlite_master
          WHERE type = 'table' AND name = 'screening_annotations')
   OR (SELECT COUNT(*) FROM screening_annotations) <> 0;
DROP TABLE _m4_guard_preconditions;

ALTER TABLE screening_runs
ADD COLUMN review_mode TEXT NOT NULL
    DEFAULT 'independent_multi_reviewer'
    CHECK (review_mode IN (
        'independent_multi_reviewer',
        'single_researcher_two_pass'
    ));

DROP TRIGGER trg_screening_annotations_guard;
DROP TRIGGER trg_screening_annotations_quote_guard;
DROP TRIGGER trg_screening_annotations_no_update;
DROP TRIGGER trg_screening_annotations_no_delete;
DROP INDEX idx_screening_annotations_evidence;
DROP INDEX idx_screening_annotations_reviewer;

CREATE TABLE screening_annotations_v4 (
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

DROP TABLE screening_annotations;
ALTER TABLE screening_annotations_v4 RENAME TO screening_annotations;

CREATE INDEX idx_screening_annotations_evidence
    ON screening_annotations (evidence_id);
CREATE INDEX idx_screening_annotations_reviewer
    ON screening_annotations (reviewer_id);
CREATE INDEX idx_screening_annotations_run_pass
    ON screening_annotations (run_id, pass_no, evidence_id);

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
       SUM(CASE WHEN field_count = 5 AND match_count = field_count
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
    UNION ALL SELECT 'directness'
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

DROP TRIGGER trg_screening_runs_manifest_guard;

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

    SELECT RAISE(ABORT, 'single_researcher_two_pass requires schema version 4 or later')
    WHERE NEW.review_mode = 'single_researcher_two_pass'
      AND NEW.schema_version < 4;

    SELECT RAISE(ABORT, 'single_researcher_two_pass is limited to pilot runs')
    WHERE NEW.review_mode = 'single_researcher_two_pass'
      AND NEW.run_type <> 'pilot';

    SELECT RAISE(ABORT, 'single_researcher_two_pass requires one identity to create, approve and approve the sample')
    WHERE NEW.review_mode = 'single_researcher_two_pass'
      AND (NEW.created_by <> NEW.approved_by
           OR NEW.sample_approved_by IS NOT NEW.approved_by);

    SELECT RAISE(ABORT, 'single_researcher_two_pass runs must be human-only')
    WHERE NEW.review_mode = 'single_researcher_two_pass'
      AND (NEW.model_provider IS NOT NULL
           OR NEW.model_id IS NOT NULL
           OR NEW.model_version IS NOT NULL
           OR NEW.embedding_model IS NOT NULL
           OR NEW.prompt_version IS NOT NULL
           OR NEW.prompt_sha256 IS NOT NULL
           OR NEW.prompt_author_id IS NOT NULL);
END;

DROP TRIGGER trg_screening_reviews_guard;

CREATE TRIGGER trg_screening_reviews_guard
BEFORE INSERT ON screening_reviews
BEGIN
    SELECT RAISE(ABORT, 'screening_reviews are not permitted in single_researcher_two_pass runs')
    WHERE (SELECT review_mode FROM screening_runs
           WHERE run_id = (SELECT run_id FROM screening_results
                           WHERE screening_id = NEW.screening_id))
          = 'single_researcher_two_pass';

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

DROP TRIGGER trg_screening_events_completion_guard;

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
              <> (SELECT screened_count * 5 FROM screening_runs
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
              <> (SELECT screened_count * 5 FROM screening_runs
                  WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(DISTINCT evidence_id) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2)
              <> (SELECT screened_count FROM screening_runs
                  WHERE run_id = NEW.run_id)
      );

    SELECT RAISE(ABORT, 'single-researcher completion requires all five fields in both passes for every sampled record')
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
              UNION ALL SELECT 'directness'
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
              <> (SELECT screened_count * 5 FROM screening_runs
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
              IS NOT (SELECT screened_count * 5 FROM screening_runs
                      WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(DISTINCT evidence_id) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 1)
              IS NOT (SELECT screened_count FROM screening_runs
                      WHERE run_id = NEW.run_id)
          OR
          (SELECT COUNT(*) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2)
              IS NOT (SELECT screened_count * 5 FROM screening_runs
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
          OR (SELECT COUNT(*) FROM json_each(NEW.detail_json, '$.agreement')) <> 6
          OR (SELECT COUNT(*) FROM json_each(NEW.detail_json, '$.agreement')
              WHERE key IN (
                  'relevance', 'directness', 'behaviour',
                  'first_time_status', 'sip_tenure', 'all_five_fields')) <> 6
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

    SELECT RAISE(ABORT, 'single-researcher sign-off all-five-fields agreement does not match stored annotations')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND (
          (SELECT json_extract(NEW.detail_json,
                               '$.agreement.all_five_fields.matches'))
              IS NOT (SELECT matches FROM v_pass_all_field_agreement
                      WHERE run_id = NEW.run_id)
          OR
          (SELECT json_extract(NEW.detail_json,
                               '$.agreement.all_five_fields.total'))
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
"""

M4_TABLES: tuple[str, ...] = M3_TABLES

M4_VIEWS: tuple[str, ...] = M3_VIEWS + (
    "v_pass_field_agreement",
    "v_pass_all_field_agreement",
    "v_run_two_pass_audit",
    "v_single_researcher_annotation_coverage",
)

M4_INDEXES: tuple[str, ...] = M3_INDEXES + (
    "idx_screening_annotations_run_pass",
)

M4_TRIGGERS: tuple[str, ...] = M3_TRIGGERS + (
    "trg_screening_annotations_pass_guard",
    "trg_screening_annotations_quote_required",
    "trg_screening_results_single_researcher_guard",
    "trg_screening_events_single_researcher_guard",
    "trg_screening_events_single_researcher_signoff",
)

M4_RECREATED_TRIGGERS: tuple[str, ...] = (
    "trg_screening_annotations_guard",
    "trg_screening_annotations_quote_guard",
    "trg_screening_annotations_no_update",
    "trg_screening_annotations_no_delete",
    "trg_screening_runs_manifest_guard",
    "trg_screening_reviews_guard",
    "trg_screening_events_completion_guard",
)

M4_LEGACY_ANNOTATION_COLUMNS: tuple[str, ...] = (
    "annotation_id",
    "run_id",
    "evidence_id",
    "reviewer_id",
    "field",
    "value",
    "quote_text",
    "quote_start",
    "quote_end",
    "quote_hash",
    "blind_to_other_reviewers",
    "blind_to_model_draft",
    "independent_of_prompt_author",
    "comment",
    "created_at",
)

__all__ = [
    "M4_SCREENING_STORE_SQL",
    "M4_TABLES",
    "M4_VIEWS",
    "M4_INDEXES",
    "M4_TRIGGERS",
    "M4_RECREATED_TRIGGERS",
    "M4_LEGACY_ANNOTATION_COLUMNS",
]
