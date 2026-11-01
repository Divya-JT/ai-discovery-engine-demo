"""Migration v10: Run 1 approved 6-hour Pass 2 interval deviation (ADR-033).

Guarded after v9. Adds an append-only run-scoped interval-deviation record and
recreates the v8 interval view/triggers so the required interval is 12 hours by
default and 6 hours only for an approved Run 1 deviation. The shipped v8 and v9
migrations are not edited. No table is rebuilt and no stored data is rewritten;
the deviation row is seeded only when Run 1 and its reviewer already exist.
"""

from __future__ import annotations

M10_INTERVAL_SQL = r"""


CREATE TABLE _m10_guard_preconditions (
    n INTEGER NOT NULL CHECK (n = 0)
);
INSERT INTO _m10_guard_preconditions (n)
SELECT 1
WHERE NOT EXISTS (
          SELECT 1 FROM schema_migrations WHERE version = 9)
   OR EXISTS (
          SELECT 1 FROM schema_migrations WHERE version = 10);
DROP TABLE _m10_guard_preconditions;

CREATE TABLE screening_interval_deviations (
    run_id          INTEGER PRIMARY KEY
                        CHECK (run_id = 1)
                        REFERENCES screening_runs(run_id)
                        ON DELETE RESTRICT,
    interval_hours  REAL    NOT NULL
                        CHECK (interval_hours = 6.0),
    decision_ref    TEXT    NOT NULL
                        CHECK (decision_ref = 'ADR-033'),
    approved_by     TEXT    NOT NULL
                        REFERENCES reviewers(reviewer_id)
                        ON DELETE RESTRICT,
    approved_at     TEXT    NOT NULL
                        CHECK (length(trim(approved_at)) > 0),
    recorded_at     TEXT    NOT NULL
                        CHECK (length(trim(recorded_at)) > 0)
);

CREATE TRIGGER trg_screening_interval_deviations_no_update
BEFORE UPDATE ON screening_interval_deviations
BEGIN
    SELECT RAISE(ABORT, 'interval deviations are append-only and cannot be updated');
END;

CREATE TRIGGER trg_screening_interval_deviations_no_delete
BEFORE DELETE ON screening_interval_deviations
BEGIN
    SELECT RAISE(ABORT, 'interval deviations are append-only and cannot be deleted');
END;

-- Seed the approved Run 1 deviation only when Run 1 and its reviewer already exist.
-- Fresh databases (including test databases) apply migrations before any run exists;
-- they receive the mechanism but no automatic deviation.
INSERT INTO screening_interval_deviations
    (run_id, interval_hours, decision_ref, approved_by, approved_at, recorded_at)
SELECT 1, 6.0, 'ADR-033', r.approved_by, '2026-11-01',
       strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
FROM screening_runs r
WHERE r.run_id = 1
  AND EXISTS (
      SELECT 1 FROM reviewers v WHERE v.reviewer_id = r.approved_by);

DROP VIEW IF EXISTS v_run_two_pass_audit;

DROP TRIGGER IF EXISTS trg_screening_events_completion_guard;

DROP TRIGGER IF EXISTS trg_screening_annotations_pass_guard;

DROP TRIGGER IF EXISTS trg_screening_pass2_imports_guard;

CREATE VIEW v_run_two_pass_audit AS
SELECT r.run_id,
       r.review_mode,
       COALESCE((SELECT d.interval_hours FROM screening_interval_deviations d WHERE d.run_id = r.run_id), 12.0) AS required_gap_hours,
       CASE WHEN EXISTS (SELECT 1 FROM screening_interval_deviations d
                         WHERE d.run_id = r.run_id) THEN 1 ELSE 0 END AS interval_deviation,
       (SELECT d.decision_ref FROM screening_interval_deviations d
        WHERE d.run_id = r.run_id) AS interval_deviation_ref,
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
           WHEN (SELECT 1 FROM screening_pass1_freezes f
                 WHERE f.run_id = r.run_id) IS NOT NULL
            AND (SELECT MIN(a.recorded_at) FROM screening_annotations a
                 WHERE a.run_id = r.run_id AND a.pass_no = 2) IS NOT NULL
           THEN ROUND((
               julianday((SELECT MIN(a.recorded_at) FROM screening_annotations a
                          WHERE a.run_id = r.run_id AND a.pass_no = 2))
               - julianday((SELECT f.frozen_at FROM screening_pass1_freezes f
                            WHERE f.run_id = r.run_id))
           ) * 24, 3)
           ELSE NULL
       END AS gap_hours,
       CASE
           WHEN (SELECT 1 FROM screening_pass1_freezes f
                 WHERE f.run_id = r.run_id) IS NOT NULL
            AND (SELECT MIN(a.recorded_at) FROM screening_annotations a
                 WHERE a.run_id = r.run_id AND a.pass_no = 2) IS NOT NULL
            AND julianday((SELECT MIN(a.recorded_at) FROM screening_annotations a
                           WHERE a.run_id = r.run_id AND a.pass_no = 2))
                - julianday((SELECT f.frozen_at FROM screening_pass1_freezes f
                             WHERE f.run_id = r.run_id)) >= COALESCE((SELECT d.interval_hours FROM screening_interval_deviations d WHERE d.run_id = r.run_id), 12.0) / 24.0
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

    SELECT RAISE(ABORT, 'single-researcher completion requires the approved Pass 1 to Pass 2 interval')
    WHERE (SELECT review_mode FROM screening_runs WHERE run_id = NEW.run_id)
              = 'single_researcher_two_pass'
      AND COALESCE(
          (SELECT julianday(MIN(recorded_at)) FROM screening_annotations
           WHERE run_id = NEW.run_id AND pass_no = 2)
          -
          (SELECT julianday(frozen_at) FROM screening_pass1_freezes
           WHERE run_id = NEW.run_id),
          0) < COALESCE((SELECT d.interval_hours FROM screening_interval_deviations d WHERE d.run_id = NEW.run_id), 12.0) / 24.0;
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

    SELECT RAISE(ABORT, 'Pass 2 requires the approved interval after the Pass 1 freeze')
    WHERE NEW.pass_no = 2
      AND julianday(NEW.recorded_at) - julianday(
              (SELECT frozen_at FROM screening_pass1_freezes
               WHERE run_id = NEW.run_id)) < COALESCE((SELECT d.interval_hours FROM screening_interval_deviations d WHERE d.run_id = NEW.run_id), 12.0) / 24.0;

    SELECT RAISE(ABORT, 'pass 2 must set blind_to_prior_pass = 1')
    WHERE NEW.pass_no = 2 AND NEW.blind_to_prior_pass <> 1;

    SELECT RAISE(ABORT, 'pass 1 has no prior pass and must set blind_to_prior_pass = 1')
    WHERE NEW.pass_no = 1 AND NEW.blind_to_prior_pass <> 1;
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

    SELECT RAISE(ABORT, 'Pass 2 import violates the approved Pass 1 to Pass 2 interval')
    WHERE EXISTS (
        SELECT 1 FROM screening_annotations
        WHERE run_id = NEW.run_id AND pass_no = 2
          AND julianday(recorded_at) - julianday(
              (SELECT frozen_at FROM screening_pass1_freezes
               WHERE run_id = NEW.run_id)) < COALESCE((SELECT d.interval_hours FROM screening_interval_deviations d WHERE d.run_id = NEW.run_id), 12.0) / 24.0);

    SELECT RAISE(ABORT, 'Pass 2 import requires blind_to_prior_pass = 1')
    WHERE EXISTS (
        SELECT 1 FROM screening_annotations
        WHERE run_id = NEW.run_id AND pass_no = 2
          AND blind_to_prior_pass <> 1);
END;
"""

M10_VIEWS: tuple[str, ...] = ("v_run_two_pass_audit",)
M10_TRIGGERS: tuple[str, ...] = (
    "trg_screening_events_completion_guard",
    "trg_screening_annotations_pass_guard",
    "trg_screening_pass2_imports_guard",
    "trg_screening_interval_deviations_no_update",
    "trg_screening_interval_deviations_no_delete",
)
M10_TABLES: tuple[str, ...] = ("screening_interval_deviations",)

__all__ = [
    "M10_INTERVAL_SQL",
    "M10_TABLES",
    "M10_VIEWS",
    "M10_TRIGGERS",
]
