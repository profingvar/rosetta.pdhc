"""#728 milestone 2 (operator decisions 2026-11-19): diagnoses from CDR
Conditions, medications and care events as presence entries, CGM as a lab
analyte. Every sample compared here was ACCEPTED by ASHA's validator on
2026-11-19; three controls (unknown path, wrong ISM state, missing diagnosis
name) were refused there."""
from __future__ import annotations

import copy
import json
import os
import sys

import pytest

from app.services import concept_map as cm
from app.services.flat_emitter import (Observation, emit_condition_compositions,
                                       emit_flat_compositions)

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_opt as bo  # noqa: E402

T = os.path.join(ROOT, "templates")
P, WHEN = "synthetic-patient-728", "2026-11-19T08:00:00Z"
MMS = "http://id.who.int/icd/release/11/mms"
METFORMIN = "53e3dfea-0023-4ab5-95a5-62b4c4c7f717"
FOOT_EXAM = "8861a054-41d2-4aca-89c5-5c3aed1cbb67"
CKD = "aecd1a50-fd77-46ae-826b-1f05266f90c3"
CGM = "22d0f6c6-2438-4637-ad4e-9d85431b8edb"
UNNAMED = "79a6cd4b-1af4-4c05-8f08-e5487a262237"
M2 = ["pdhc_diagnoses.v1", "pdhc_medications.v1", "pdhc_care_events.v1"]


def _sample(name):
    return json.load(open(os.path.join(T, "samples", name)))


def _presence_rows():
    rows = [Observation(g, None, WHEN, P, answer=True) for g in cm.mapped_guids()
            if cm.resolve(g).binding_kind == "presence_entry"]
    rows.append(Observation(METFORMIN, None, WHEN, P, answer=False))   # a "no" too
    return rows


def _cond(i, code, clin="active", ver="confirmed", **kw):
    c = {"resourceType": "Condition", "id": f"c{i}", "subject": {"reference": f"Patient/{P}"},
         "clinicalStatus": {"coding": [{"code": clin}]},
         "verificationStatus": {"coding": [{"code": ver}]},
         "code": {"coding": [{"system": MMS, "version": "2026-01", "code": code,
                              "display": kw.pop("display", code)}] + kw.pop("extra", [])},
         "recordedDate": "2026-03-02T10:00:00Z"}
    c.update(kw)
    return c


SAMPLE_CONDITIONS = [
    _cond(1, "5A11", display="Type 2 diabetes mellitus", onsetDateTime="2019-05-01"),
    _cond(2, "BA00.Z", display="Essential hypertension, unspecified"),
    _cond(3, "GB61.Z", display="Chronic kidney disease, stage unspecified"),
    _cond(4, "BD10", clin="resolved", display="Congestive heart failure"),
    _cond(5, "5A10", ver="refuted", display="Type 1 diabetes mellitus"),
]


# --- templates ------------------------------------------------------------

@pytest.mark.parametrize("tid", M2)
def test_committed_opt_is_the_generated_one(tid):
    src = bo.load_source(os.path.join(T, "src", tid + ".json"))
    assert bo.build(src) == open(os.path.join(T, tid + ".opt"), "rb").read()


@pytest.mark.parametrize("tid", M2)
def test_xsd_valid(tid):
    pytest.importorskip("lxml")
    assert bo.xsd_errors(open(os.path.join(T, tid + ".opt"), "rb").read()) == []


def test_action_ism_alternatives_can_be_narrowed_but_a_mandatory_node_still_cannot():
    arch = bo.load_archetype("openEHR-EHR-ACTION.procedure.v1")
    d = copy.deepcopy(arch.definition)
    bo.prune(d, ["/ism_transition[at0043]", "/description[at0001]/items[at0002]"])
    ism = [a for a in d.findall("attributes") if a.findtext("rm_attribute_name") == "ism_transition"][0]
    assert [c.findtext("node_id") for c in ism.findall("children")] == ["at0043"]
    with pytest.raises(bo.BuildError, match="mandatory"):   # Procedure name is 1..1 in a MULTIPLE attribute
        bo.prune(copy.deepcopy(arch.definition), ["/ism_transition[at0043]", "/description[at0001]/items[at0049]"])


@pytest.mark.parametrize("tid", M2)
def test_every_emitted_leaf_is_a_path_asha_reported(tid):
    """Each data key the samples send exists in the Explore capture (index and
    attribute suffix stripped); ACTION time/ISM are RM attributes Explore omits."""
    reported = {l.strip() for l in open(os.path.join(T, tid + ".sandbox_paths.txt")) if l.startswith("pdhc_")}
    flat = _sample(tid.replace(".v1", "_valid.flat.json"))
    root = cm.flat_root(tid)
    for k in flat:
        if k.startswith(root + "/") and k.count("/") >= 3 and "/ism_transition/" not in k and not k.endswith("/time"):
            leaf = "/".join(p.split(":")[0] for p in k.split("|")[0].split("/"))
            assert leaf in reported, k


# --- presence entries -----------------------------------------------------

def test_presence_samples_match_what_asha_accepted():
    for order in (1, -1):
        res = emit_flat_compositions(_presence_rows()[::order], strict=True)
        by = {c["template_id"]: c["flat"] for c in res.compositions}
        assert by["pdhc_medications.v1"] == _sample("pdhc_medications_valid.flat.json")
        assert by["pdhc_care_events.v1"] == _sample("pdhc_care_events_valid.flat.json")


def test_a_no_is_never_emitted():
    res = emit_flat_compositions([Observation(METFORMIN, None, WHEN, P, answer=False)], strict=True)
    assert res.compositions == [] and res.not_present == [METFORMIN]


@pytest.mark.parametrize("answer", [None, "unknown", 0, "no"])
def test_anything_but_yes_is_not_emitted(answer):
    res = emit_flat_compositions([Observation(FOOT_EXAM, None, WHEN, P, answer=answer)], strict=True)
    assert res.compositions == []


def test_a_yes_arrives_in_the_gateway_canonical_block():
    class Row:   # an ObservationCache row: value is NULL for a boolean, the answer is in raw
        concept_guid, value, observed_at, patient_guid, unit = METFORMIN, None, WHEN, P, None
        raw = {"extension": [{"url": "urn:pdhc:fhir:extension:canonical-observation",
                              "valueString": json.dumps({"value": True})}]}
    res = emit_flat_compositions([Row()], strict=True)
    flat = res.compositions[0]["flat"]
    assert flat["pdhc_medications/medication_summary:0/medication_name|code"] == "A10BA02"
    assert flat["pdhc_medications/medication_summary:0/medication_name|terminology"] == "ATC"


def test_medications_are_atc_coded():
    for g in cm.mapped_guids():
        b = cm.resolve(g)
        if b.template_id == "pdhc_medications.v1":
            assert b.terminology == "ATC" and b.code[0] in "ABC" and b.value_kind == "DV_CODED_TEXT"


# --- diagnoses from CDR Conditions -----------------------------------------

def test_diagnosis_sample_matches_what_asha_accepted():
    res = emit_condition_compositions(SAMPLE_CONDITIONS, at=WHEN)
    assert [c["flat"] for c in res.compositions] == [_sample("pdhc_diagnoses_valid.flat.json")]


def test_only_present_diagnoses_are_emitted():
    res = emit_condition_compositions(SAMPLE_CONDITIONS, at=WHEN)
    flat = res.compositions[0]["flat"]
    codes = {v for k, v in flat.items() if k.endswith("problem_diagnosis_name|code")}
    assert codes == {"5A11", "BA00.Z", "GB61.Z"}
    assert sorted(res.not_present) == ["c4", "c5"]   # resolved, refuted


def test_entered_in_error_and_inactive_are_not_present():
    res = emit_condition_compositions([_cond(1, "5A11", ver="entered-in-error"),
                                       _cond(2, "5A11", clin="inactive"),
                                       _cond(3, "5A11", clin="remission")], at=WHEN)
    assert res.compositions == [] and len(res.not_present) == 3


def test_a_non_icd11_code_is_not_sent_as_icd11():
    c = _cond(1, "E11.9")
    c["code"]["coding"][0]["system"] = "https://termbank.pdhc.se/CodeSystem/icd-10-se"
    res = emit_condition_compositions([c], at=WHEN)
    assert res.compositions == [] and res.not_icd11 == ["c1"]


def test_the_who_coding_wins_over_the_simplified_one():
    simplified = {"extension": [{"url": "https://icd11.pdhc.se/fhir/StructureDefinition/simplified-equivalent",
                                 "valueBoolean": True}], "system": MMS, "code": "5A1Z", "display": "x"}
    res = emit_condition_compositions([_cond(1, "5A11", extra=[simplified])], at=WHEN)
    assert res.compositions[0]["flat"]["pdhc_diagnoses/problem_diagnosis:0/problem_diagnosis_name|code"] == "5A11"


def test_a_plan_answer_for_a_diagnosis_is_sourced_elsewhere_not_unmapped():
    res = emit_flat_compositions([Observation(CKD, None, WHEN, P, answer=True)], strict=True)
    assert res.compositions == [] and res.sourced_elsewhere == [CKD] and res.unmapped == []


# --- CGM and the one gap -----------------------------------------------------

def test_cgm_is_a_lab_analyte_with_the_verified_cgm_loinc():
    b = cm.resolve(CGM)
    assert b.template_id == "pdhc_laboratory.v1" and b.binding_kind == "repeating_analyte"
    assert b.ucum == "mmol/L" and b.loinc == "105272-9"


def test_the_unnamed_concept_stays_a_gap():
    assert not cm.is_mapped(UNNAMED) and not cm.is_condition_sourced(UNNAMED)
    assert "name it" in cm.pending_binding(UNNAMED)["note"]
