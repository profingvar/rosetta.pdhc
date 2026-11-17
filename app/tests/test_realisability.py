"""openEHR-realisability check (#523) — service + endpoint."""
from app import create_app
from app.services import realisability as r

BP_SYSTOLIC = "64928bff-9a46-472a-bbf1-dcfc694f945b"
BP_DIASTOLIC = "fb6487d7-b473-4ace-bb39-6352d4497009"
TEMPERATURE = "d7d81372-1c61-4459-88fe-5878d6586e64"
PENDING = "aecd1a50-fd77-46ae-826b-1f05266f90c3"    # ckd_diagnosis: gaps.pending_template_extension (milestone 2, #728)
WEIGHT = "465e9554-8be6-48f2-9e1a-154d4e1f911e"          # bound to pdhc_anthropometry.v1 (#728)
UNMAPPED = "00000000-0000-0000-0000-000000000000"


def test_mapped_concept_is_realisable():
    c = r.check_concept(BP_SYSTOLIC)
    assert c["status"] == "realisable"
    assert c["realisable"] is True
    assert c["template_id"] == "pdhc_vitals.v1"
    assert c["value_kind"] == "DV_QUANTITY"
    assert c["ucum"] == "mm[Hg]"
    assert c["blockers"] == []


def test_pending_concept_reports_pending_not_unmapped():
    c = r.check_concept(PENDING)
    assert c["status"] == "pending"
    assert c["realisable"] is False
    assert c.get("target_archetype")
    assert c["blockers"]


def test_unmapped_concept_is_flagged():
    c = r.check_concept(UNMAPPED)
    assert c["status"] == "unmapped"
    assert c["realisable"] is False
    assert c["blockers"]


def test_plandef_rollup_mixes_states():
    res = r.check_plandef([BP_SYSTOLIC, BP_DIASTOLIC, PENDING, UNMAPPED, BP_SYSTOLIC])
    assert res["total"] == 4                       # dedup dropped the repeat
    assert res["realisable_count"] == 2
    assert res["all_realisable"] is False
    assert res["templates"] == ["pdhc_vitals.v1"]
    assert PENDING in res["pending"]
    assert UNMAPPED in res["unmapped"]


def test_all_realisable_true_when_every_concept_maps():
    res = r.check_plandef([BP_SYSTOLIC, TEMPERATURE])
    assert res["all_realisable"] is True
    assert res["blocked_count"] == 0


# ---- endpoint ----------------------------------------------------------
def _client(**cfg):
    app = create_app(cfg or None)
    return app.test_client()


def test_endpoint_accepts_concept_guids():
    c = _client()
    resp = c.post("/api/v1/openehr/realisable", json={"concept_guids": [BP_SYSTOLIC, UNMAPPED]})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["total"] == 2 and body["realisable_count"] == 1


def test_endpoint_accepts_plandef_shape():
    c = _client()
    resp = c.post("/api/v1/openehr/realisable",
                  json={"transactions": [{"concept_guid": BP_SYSTOLIC}],
                        "goals": [{"concept_guid": TEMPERATURE}]})
    assert resp.status_code == 200
    assert resp.get_json()["all_realisable"] is True


def test_endpoint_requires_service_key_when_configured():
    c = _client(ROSETTA_SERVICE_KEY="secret", AUTH_MODE="off")
    # no key -> 401
    assert c.post("/api/v1/openehr/realisable", json={"concept_guids": []}).status_code == 401
    # right key -> 200
    ok = c.post("/api/v1/openehr/realisable", json={"concept_guids": [BP_SYSTOLIC]},
                headers={"X-Service-Key": "secret"})
    assert ok.status_code == 200


def test_weight_is_realisable_in_anthropometry_728():
    c = r.check_concept(WEIGHT)
    assert c["status"] == "realisable"
    assert c["template_id"] == "pdhc_anthropometry.v1"
    assert c["flat_path"] == "pdhc_anthropometry/body_weight/any_event/weight"
    assert c["ucum"] == "kg"


def test_lab_and_fev1_are_realisable_728():
    for g, tid in (("ff07c0fd-d4e2-49aa-b32b-cda5d24559a6", "pdhc_laboratory.v1"),
                   ("6521528c-db59-45c5-a492-003c28f27623", "pdhc_lung_function.v1")):
        c = r.check_concept(g)
        assert c["status"] == "realisable" and c["template_id"] == tid
