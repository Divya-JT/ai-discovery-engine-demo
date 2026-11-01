"""Migration v9: structural validation for reconciliation-v2 sign-off.

Trigger-only migration. It does not rebuild tables or rewrite stored data.
The trigger prevents structural forgery; the Python verifier remains mandatory
for canonical digest and semantic checks (A44 §4.5).
"""

from __future__ import annotations

M9_SIGNOFF_SQL = r"""

CREATE TABLE _m9_guard_preconditions (
    n INTEGER NOT NULL CHECK (n = 0)
);
INSERT INTO _m9_guard_preconditions (n)
SELECT 1
WHERE NOT EXISTS (
          SELECT 1 FROM schema_migrations WHERE version = 8)
   OR EXISTS (
          SELECT 1 FROM schema_migrations WHERE version = 9);
DROP TABLE _m9_guard_preconditions;

DROP TRIGGER IF EXISTS trg_screening_events_signoff_v2_guard;

CREATE TRIGGER trg_screening_events_signoff_v2_guard
BEFORE INSERT ON screening_events
WHEN NEW.event_type = 'review_completed'
 AND json_extract(NEW.detail_json, '$.reconciliation_schema') = 'm4-reconciliation-v2'
BEGIN
    SELECT RAISE(ABORT, 'v2 sign-off requires a reconciliation object')
    WHERE json_type(NEW.detail_json, '$.reconciliation') IS NOT 'object';

    SELECT RAISE(ABORT, 'v2 sign-off requires a records array')
    WHERE json_type(NEW.detail_json, '$.reconciliation.records') IS NOT 'array';

    SELECT RAISE(ABORT, 'v2 sign-off changed_records must be an integer')
    WHERE json_type(NEW.detail_json, '$.changed_records') IS NOT 'integer';

    SELECT RAISE(ABORT, 'v2 sign-off records length must equal changed_records')
    WHERE json_array_length(NEW.detail_json, '$.reconciliation.records')
          IS NOT json_extract(NEW.detail_json, '$.changed_records');

    SELECT RAISE(ABORT, 'v2 sign-off reconciled_evidence_ids must be an array')
    WHERE json_type(NEW.detail_json, '$.reconciled_evidence_ids') IS NOT 'array';

    SELECT RAISE(ABORT, 'v2 sign-off requires a 64-hex reconciliation digest')
    WHERE json_type(NEW.detail_json, '$.reconciliation_artifact_sha256') IS NOT 'text'
       OR length(json_extract(NEW.detail_json, '$.reconciliation_artifact_sha256')) <> 64
       OR json_extract(NEW.detail_json, '$.reconciliation_artifact_sha256')
            GLOB '*[^0-9a-f]*';

    SELECT RAISE(ABORT, 'v2 sign-off record must be an object')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r
        WHERE json_type(r.value) IS NOT 'object');

    SELECT RAISE(ABORT, 'v2 sign-off record evidence_id is required')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r
        WHERE json_type(r.value, '$.evidence_id') IS NOT 'text'
           OR length(trim(json_extract(r.value, '$.evidence_id'))) = 0);

    SELECT RAISE(ABORT, 'v2 sign-off record evidence IDs must be unique and reconciled')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r
        WHERE NOT EXISTS (
            SELECT 1 FROM json_each(NEW.detail_json, '$.reconciled_evidence_ids') e
            WHERE e.value = json_extract(r.value, '$.evidence_id')));

    SELECT RAISE(ABORT, 'v2 sign-off record evidence IDs must be unique')
    WHERE EXISTS (
        SELECT 1 FROM (
            SELECT json_extract(r.value, '$.evidence_id') AS evidence_id
            FROM json_each(NEW.detail_json, '$.reconciliation.records') r
            GROUP BY evidence_id HAVING COUNT(*) > 1));

    SELECT RAISE(ABORT, 'v2 sign-off record must contain pass_1, pass_2, final and fields objects')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r
        WHERE json_type(r.value, '$.pass_1') IS NOT 'object'
           OR json_type(r.value, '$.pass_2') IS NOT 'object'
           OR json_type(r.value, '$.final') IS NOT 'object'
           OR json_type(r.value, '$.fields') IS NOT 'object');

    SELECT RAISE(ABORT, 'v2 sign-off record must contain exactly four fields')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r
        WHERE (SELECT COUNT(*) FROM json_each(r.value, '$.fields')) <> 4
           OR json_type(r.value, '$.fields.relevance') IS NOT 'object'
           OR json_type(r.value, '$.fields.behaviour') IS NOT 'object'
           OR json_type(r.value, '$.fields.first_time_status') IS NOT 'object'
           OR json_type(r.value, '$.fields.sip_tenure') IS NOT 'object');

    SELECT RAISE(ABORT, 'v2 sign-off record must contain four pass_1 values')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r
        WHERE (SELECT COUNT(*) FROM json_each(r.value, '$.pass_1')) <> 4
           OR json_type(r.value, '$.pass_1.relevance') IS NOT 'text'
           OR json_type(r.value, '$.pass_1.behaviour') IS NOT 'text'
           OR json_type(r.value, '$.pass_1.first_time_status') IS NOT 'text'
           OR json_type(r.value, '$.pass_1.sip_tenure') IS NOT 'text');

    SELECT RAISE(ABORT, 'v2 sign-off record must contain four pass_2 values')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r
        WHERE (SELECT COUNT(*) FROM json_each(r.value, '$.pass_2')) <> 4
           OR json_type(r.value, '$.pass_2.relevance') IS NOT 'text'
           OR json_type(r.value, '$.pass_2.behaviour') IS NOT 'text'
           OR json_type(r.value, '$.pass_2.first_time_status') IS NOT 'text'
           OR json_type(r.value, '$.pass_2.sip_tenure') IS NOT 'text');

    SELECT RAISE(ABORT, 'v2 sign-off record must contain four final values')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r
        WHERE (SELECT COUNT(*) FROM json_each(r.value, '$.final')) <> 4
           OR json_type(r.value, '$.final.relevance') IS NOT 'text'
           OR json_type(r.value, '$.final.first_time_status') IS NOT 'text'
           OR json_type(r.value, '$.final.sip_tenure') IS NOT 'text'
           OR json_type(r.value, '$.final.behaviour') IS NULL
           OR json_type(r.value, '$.final.behaviour') NOT IN ('text', 'null'));

    SELECT RAISE(ABORT, 'v2 sign-off field changed flag must be boolean')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_type(f.value, '$.changed') NOT IN ('true', 'false'));

    SELECT RAISE(ABORT, 'v2 sign-off researcher_decision must be one of four decisions')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_type(f.value, '$.researcher_decision') IS NOT 'text'
           OR json_extract(f.value, '$.researcher_decision') NOT IN (
                  'follow_pass_1', 'follow_pass_2', 'corrected_reading', 'unchanged'));

    SELECT RAISE(ABORT, 'v2 sign-off changed field cannot be unchanged')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_extract(f.value, '$.changed') = 1
          AND json_extract(f.value, '$.researcher_decision') = 'unchanged');

    SELECT RAISE(ABORT, 'v2 sign-off unchanged field must use unchanged')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_extract(f.value, '$.changed') = 0
          AND json_extract(f.value, '$.researcher_decision') <> 'unchanged');

    SELECT RAISE(ABORT, 'v2 sign-off normalization_outcome is invalid')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_type(f.value, '$.normalization_outcome') IS NOT 'text'
           OR json_extract(f.value, '$.normalization_outcome') NOT IN (
                  'normal', 'normalized_by_final_relevance'));

    SELECT RAISE(ABORT, 'v2 sign-off normalisation object must match normalization_outcome')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE (json_extract(f.value, '$.normalization_outcome') = 'normal'
               AND json_type(f.value, '$.normalization') IS NOT 'null')
           OR (json_extract(f.value, '$.normalization_outcome') = 'normalized_by_final_relevance'
               AND json_type(f.value, '$.normalization') IS NOT 'object'));

    SELECT RAISE(ABORT, 'v2 sign-off normalisation must contain all Q1 fields')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_extract(f.value, '$.normalization_outcome') = 'normalized_by_final_relevance'
          AND (json_type(f.value, '$.normalization.affected_field') IS NOT 'text'
               OR json_type(f.value, '$.normalization.pass_1_value') IS NOT 'text'
               OR json_type(f.value, '$.normalization.pass_2_value') IS NOT 'text'
               OR json_type(f.value, '$.normalization.final_normalised_value') NOT IN ('text', 'null')
               OR json_type(f.value, '$.normalization.normalisation_reason') IS NOT 'text'
               OR json_type(f.value, '$.normalization.applicable_reconciliation_decision') IS NOT 'text'
               OR json_type(f.value, '$.normalization.explicit_signal_discarded') NOT IN ('true', 'false')));

    SELECT RAISE(ABORT, 'v2 sign-off normalisation values must agree with pass/final values')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_extract(f.value, '$.normalization_outcome') = 'normalized_by_final_relevance'
          AND (json_extract(f.value, '$.normalization.affected_field') IS NOT f.key
               OR json_extract(f.value, '$.normalization.pass_1_value')
                  IS NOT json_extract(r.value, '$.pass_1.' || f.key)
               OR json_extract(f.value, '$.normalization.pass_2_value')
                  IS NOT json_extract(r.value, '$.pass_2.' || f.key)
               OR json_extract(f.value, '$.normalization.final_normalised_value')
                  IS NOT json_extract(r.value, '$.final.' || f.key)));

    SELECT RAISE(ABORT, 'v2 sign-off normalisation_reason is invalid for the affected field')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_extract(f.value, '$.normalization_outcome') = 'normalized_by_final_relevance'
          AND ((f.key = 'behaviour'
                AND json_extract(f.value, '$.normalization.normalisation_reason')
                    <> 'unrelated_or_insufficient_forces_null_behaviour')
               OR (f.key <> 'behaviour'
                   AND json_extract(f.value, '$.normalization.normalisation_reason')
                       <> 'unrelated_or_insufficient_forces_not_established')));

    SELECT RAISE(ABORT, 'v2 sign-off applicable_reconciliation_decision must be one of four decisions')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_extract(f.value, '$.normalization_outcome') = 'normalized_by_final_relevance'
          AND (json_extract(f.value, '$.normalization.applicable_reconciliation_decision') NOT IN (
                   'follow_pass_1', 'follow_pass_2', 'corrected_reading', 'unchanged')
               OR json_extract(f.value, '$.normalization.applicable_reconciliation_decision')
                  IS NOT json_extract(f.value, '$.researcher_decision')));

    SELECT RAISE(ABORT, 'v2 sign-off reason_category is required for a changed decision')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_extract(f.value, '$.researcher_decision') <> 'unchanged'
          AND (json_type(f.value, '$.reason_category') IS NOT 'text'
               OR json_extract(f.value, '$.reason_category') NOT IN (
                      'codebook_ambiguity', 'reading_error', 'context_effect', 'data_provenance')));

    SELECT RAISE(ABORT, 'v2 sign-off corrected_reading requires evidence_basis and comment')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_extract(f.value, '$.researcher_decision') = 'corrected_reading'
          AND (json_type(f.value, '$.evidence_basis') IS NOT 'text'
               OR json_extract(f.value, '$.evidence_basis') NOT IN (
                      'final_result_quote', 'final_result_rationale', 'schema_normalisation')
               OR json_type(f.value, '$.comment') IS NOT 'text'
               OR length(trim(json_extract(f.value, '$.comment'))) = 0));

    SELECT RAISE(ABORT, 'v2 sign-off behaviour correction requires a stance')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE f.key = 'behaviour'
          AND json_extract(f.value, '$.researcher_decision') = 'corrected_reading'
          AND json_extract(r.value, '$.final.relevance') IN ('relevant', 'indirectly_relevant')
          AND (json_type(f.value, '$.stance') IS NOT 'text'
               OR json_extract(f.value, '$.stance') NOT IN (
                      'observed_action', 'intention', 'hypothetical', 'second_hand',
                      'quoted_advice', 'sarcasm_negation', 'not_applicable')
               OR json_extract(f.value, '$.stance') = 'not_applicable'));

    SELECT RAISE(ABORT, 'v2 sign-off explicit discarded signal requires a comment')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r,
                      json_each(r.value, '$.fields') f
        WHERE json_extract(f.value, '$.normalization_outcome') = 'normalized_by_final_relevance'
          AND json_extract(f.value, '$.normalization.explicit_signal_discarded') = 1
          AND (json_type(f.value, '$.comment') IS NOT 'text'
               OR length(trim(json_extract(f.value, '$.comment'))) = 0));

    SELECT RAISE(ABORT, 'v2 sign-off record_outcome is invalid')
    WHERE EXISTS (
        SELECT 1 FROM json_each(NEW.detail_json, '$.reconciliation.records') r
        WHERE json_type(r.value, '$.record_outcome') IS NOT 'text'
           OR json_extract(r.value, '$.record_outcome') NOT IN (
                  'follow_pass_1', 'follow_pass_2', 'corrected_reading', 'unchanged', 'mixed'));
END;
"""

__all__ = ["M9_SIGNOFF_SQL"]
