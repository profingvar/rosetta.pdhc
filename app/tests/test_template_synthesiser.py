"""PlanDef -> openEHR template synthesiser (#524) — service + endpoint."""
from app import create_app
from app.services import template_synthesiser as ts

BP_SYSTOLIC = "64928bff-9a46-472a-bbf1-dcfc694f945b"   # realisable -> pdhc_vitals.v1
TEMPERATURE = "d7d81372-1c61-4459-88fe-5878d6586e64"   # realisable -> pdhc_vitals.v1
LEUKOCYTES = "ff07c0fd-d4e2-49aa-b32b-cda5d24559a6"    # realisable -> pdhc_laboratory.v1 (#728)
WEIGHT = "465e9554-8be6-48f2-9e1a-154d4e1f911e"        # realisable -> pdhc_anthropometry.v1 (#728)
FEV1 = "6521528c-db59-45c5-a492-003c28f27623"          # realisable -> pdhc_lung_function.v1 (#728)
CKD = "aecd1a50-fd77-46ae-826b-1f05266f90c3"           # #728 M2: ready, rendered from CDR Conditions
UNNAMED = "79a6cd4b-1af4-4c05-8f08-e5487a262237"       # pending: the plan concept has no name
UNMAPPED = "00000000-0000-0000-0000-000000000000"


def _tmpl(res, tid):
    return next((t for t in res["templates"] if t["template_id"] == tid), None)


def test_vitals_only_is_ready_and_fully_synthesisable():
    res = ts.synthesise([BP_SYSTOLIC, TEMPERATURE])
    assert res["templates_ready"] == ["pdhc_vitals.v1"]
    assert res["summary"]["fully_synthesisable"] is True
    v = _tmpl(res, "pdhc_vitals.v1")
    assert v["status"] == "ready"
    assert "flat_root" in v  # the one authored template exposes its FLAT contract


def test_mixed_groups_by_template_and_flags_to_author():
    res = ts.synthesise([BP_SYSTOLIC, LEUKOCYTES, WEIGHT, FEV1, UNNAMED, UNMAPPED])
    assert set(res["templates_ready"]) == {"pdhc_vitals.v1", "pdhc_anthropometry.v1",
                                           "pdhc_laboratory.v1", "pdhc_lung_function.v1"}
    assert set(res["templates_to_author"]) == {"pdhc_diagnoses.v1"}   # only the unnamed concept
    a = _tmpl(res, "pdhc_anthropometry.v1")
    assert a["flat_root"] == "pdhc_anthropometry"   # its own FLAT contract, not vitals'

    assert res["summary"]["fully_synthesisable"] is False
    assert any(u["concept_guid"] == UNMAPPED for u in res["unmapped"])


def test_lab_template_carries_its_archetype():
    res = ts.synthesise([LEUKOCYTES])
    lab = _tmpl(res, "pdhc_laboratory.v1")
    assert lab["status"] == "ready"   # #728: authored, accepted by ASHA
    assert lab["flat_root"] == "pdhc_laboratory"
    assert lab["concepts"][0]["status"] == "realisable"


def test_dedup_counts_once():
    res = ts.synthesise([BP_SYSTOLIC, BP_SYSTOLIC])
    assert res["summary"]["concepts"] == 1


# ---- endpoint ----------------------------------------------------------
def _client(**cfg):
    return create_app(cfg or None).test_client()


def test_endpoint_concept_guids():
    r = _client().post("/api/v1/openehr/template-spec",
                       json={"concept_guids": [BP_SYSTOLIC, LEUKOCYTES]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["summary"]["templates_total"] == 2


def test_endpoint_plandef_shape():
    r = _client().post("/api/v1/openehr/template-spec",
                       json={"transactions": [{"concept_guid": BP_SYSTOLIC},
                                              {"concept_guid": TEMPERATURE}]})
    assert r.status_code == 200
    assert r.get_json()["templates_ready"] == ["pdhc_vitals.v1"]


def test_endpoint_service_key_enforced():
    c = _client(ROSETTA_SERVICE_KEY="secret", AUTH_MODE="off")
    assert c.post("/api/v1/openehr/template-spec", json={"concept_guids": []}).status_code == 401
    ok = c.post("/api/v1/openehr/template-spec", json={"concept_guids": [BP_SYSTOLIC]},
                headers={"X-Service-Key": "secret"})
    assert ok.status_code == 200


def test_diagnoses_are_ready_from_cdr_conditions_728():
    res = ts.synthesise([CKD])
    d = _tmpl(res, "pdhc_diagnoses.v1")
    assert d["status"] == "ready" and d["flat_root"] == "pdhc_diagnoses"
    assert d["concepts"][0]["source"] == "cdr_condition"
