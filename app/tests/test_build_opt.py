"""#728 — the OPT generator (scripts/build_opt.py) and its generated templates.

The strongest control is the first test: the generator regenerates the
sandbox-ACCEPTED ``pdhc_vitals.v1.opt`` (an Archetype Designer export) byte for
byte from its source definition and the vendored CKM archetypes. Everything the
anthropometry template relies on — the dialect, the namespace placement, the
ontology ordering — is exercised by that test on a document a CDR accepted.
"""
import copy
import glob
import hashlib
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import build_opt as bo  # noqa: E402

T = os.path.join(_ROOT, "templates")
SRC = os.path.join(T, "src")
VITALS_SRC = os.path.join(SRC, "pdhc_vitals.v1.json")
ANTHRO_SRC = os.path.join(SRC, "pdhc_anthropometry.v1.json")
VITALS_OPT = os.path.join(T, "pdhc_vitals.v1.opt")
ANTHRO_OPT = os.path.join(T, "pdhc_anthropometry.v1.opt")
EXPECTED = os.path.join(T, "pdhc_anthropometry.v1.expected_paths.json")
LUNG_SRC = os.path.join(SRC, "pdhc_lung_function.v1.json")
LAB_SRC = os.path.join(SRC, "pdhc_laboratory.v1.json")
LUNG_OPT = os.path.join(T, "pdhc_lung_function.v1.opt")
LAB_OPT = os.path.join(T, "pdhc_laboratory.v1.opt")

O = "{http://schemas.openehr.org/v1}"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _read(p):
    with open(p, "rb") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def anthro_bytes():
    return bo.build(bo.load_source(ANTHRO_SRC))


@pytest.fixture(scope="module")
def anthro_root(anthro_bytes):
    return ET.fromstring(anthro_bytes)


def _roots(root):
    return [c for c in root.iter(O + "children") if c.get(XSI) == "C_ARCHETYPE_ROOT"]


# --- the control ------------------------------------------------------------
def test_vitals_opt_regenerates_byte_for_byte():
    assert bo.build(bo.load_source(VITALS_SRC)) == _read(VITALS_OPT)


def test_committed_anthropometry_opt_is_the_generated_one(anthro_bytes):
    assert _read(ANTHRO_OPT) == anthro_bytes


# --- determinism ------------------------------------------------------------
def test_build_is_deterministic_and_independent_of_key_order(anthro_bytes):
    src = bo.load_source(ANTHRO_SRC)
    assert bo.build(src) == anthro_bytes
    shuffled = json.loads(json.dumps(src, sort_keys=True))
    for item in shuffled["content"]:
        item["constrain"] = dict(reversed(list(item["constrain"].items())))
    assert bo.build(shuffled) == anthro_bytes


def test_cli_check_mode(tmp_path):
    assert bo.main([ANTHRO_SRC, "--check", ANTHRO_OPT]) == 0
    other = tmp_path / "x.opt"
    other.write_bytes(_read(ANTHRO_OPT).replace(b"PDHC Anthropometry", b"PDHC Anthropometrx"))
    assert bo.main([ANTHRO_SRC, "--check", str(other)]) == 1


# --- well-formed + XSD ------------------------------------------------------
def test_well_formed(anthro_root):
    assert anthro_root.tag == O + "template"
    assert anthro_root.findtext(O + "template_id/" + O + "value") == "pdhc_anthropometry.v1"
    assert anthro_root.findtext(O + "concept") == "PDHC Anthropometry"


def test_xsd_valid_with_controls(anthro_bytes):
    pytest.importorskip("lxml", reason="lxml not installed in this venv; run build_opt.py --xsd with an lxml python")
    assert bo.xsd_errors(_read(VITALS_OPT)) == []          # positive control: the accepted OPT
    assert bo.xsd_errors(anthro_bytes) == []
    broken = re.sub(rb"<concept>.*?</concept>\n", b"", anthro_bytes, count=1)
    assert broken != anthro_bytes and bo.xsd_errors(broken)   # negative control


# --- units ------------------------------------------------------------------
EXPECTED_UNITS = {
    "openEHR-EHR-OBSERVATION.body_weight.v2": ("124", ["kg"]),
    "openEHR-EHR-OBSERVATION.height.v2": ("122", ["cm"]),
    "openEHR-EHR-OBSERVATION.body_mass_index.v2": ("349", ["kg/m2"]),
    "openEHR-EHR-OBSERVATION.waist_circumference.v1": ("122", ["cm"]),
}


def test_each_observation_has_exactly_its_unit(anthro_root):
    roots = _roots(anthro_root)
    assert [r.findtext(O + "archetype_id/" + O + "value") for r in roots] == list(EXPECTED_UNITS)
    for r in roots:
        prop, units = EXPECTED_UNITS[r.findtext(O + "archetype_id/" + O + "value")]
        qs = [c for c in r.iter(O + "children") if c.get(XSI) == "C_DV_QUANTITY"]
        assert len(qs) == 1
        assert qs[0].findtext(O + "property/" + O + "code_string") == prop
        assert [l.findtext(O + "units") for l in qs[0].findall(O + "list")] == units


def test_units_match_the_concept_map_ucum():
    # #728: the five are bound live now, each naming its archetype
    concepts = json.load(open(os.path.join(T, "pdhc_concept_map.json")))["concepts"]
    src = bo.load_source(ANTHRO_SRC)
    by_arch = {i["archetype_id"]: list(i["constrain"].values())[0]["units"] for i in src["content"]}
    anthro = [v for v in concepts.values()
              if isinstance(v, dict) and v.get("template_id") == "pdhc_anthropometry.v1"]
    assert len(anthro) == 5
    for v in anthro:
        assert by_arch[v["archetype"]] == [v["ucum"]]


def test_a_unit_the_archetype_does_not_allow_is_refused():
    src = bo.load_source(ANTHRO_SRC)
    path = list(src["content"][0]["constrain"])[0]
    src["content"][0]["constrain"][path]["units"] = ["mg"]
    with pytest.raises(bo.BuildError, match="not allowed by the archetype"):
        bo.build(src)


def test_pruning_refuses_to_drop_a_mandatory_node():
    arch = bo.load_archetype("openEHR-EHR-OBSERVATION.body_weight.v2")
    d = copy.deepcopy(arch.definition)
    with pytest.raises(bo.BuildError, match="mandatory"):
        bo.prune(d, ["/data[at0002]/events[at0003]/data[at0001]/items[at0024]"])   # drops at0004 (1..1)


def test_a_keep_path_that_does_not_exist_is_refused():
    src = bo.load_source(ANTHRO_SRC)
    src["content"][0]["keep"] = ["/data[at0002]/events[at0003]/data[at0001]/items[at9999]"]
    src["content"][0]["constrain"] = {}
    with pytest.raises(bo.BuildError):
        bo.build(src)


def test_pruned_to_any_event_and_the_one_element(anthro_root):
    for r in _roots(anthro_root):
        assert [a.findtext(O + "rm_attribute_name") for a in r.findall(O + "attributes")] == ["data"]
        events = [c for c in r.iter(O + "children")
                  if c.findtext(O + "rm_type_name") in ("EVENT", "POINT_EVENT", "INTERVAL_EVENT")]
        assert len(events) == 1 and events[0].findtext(O + "rm_type_name") == "EVENT"
        assert [a.findtext(O + "rm_attribute_name") for a in events[0].findall(O + "attributes")] == ["data"]
        elements = [c for c in r.iter(O + "children") if c.findtext(O + "rm_type_name") == "ELEMENT"]
        assert [e.findtext(O + "node_id") for e in elements] == ["at0004"]


# --- structure: same skeleton as the accepted vitals OPT --------------------
def _skeleton(path):
    root = ET.parse(path).getroot()
    for a in root.iter(O + "attributes"):
        if a.findtext(O + "rm_attribute_name") == "content":
            for c in a.findall(O + "children"):
                a.remove(c)
    for td in root.find(O + "definition").findall(O + "term_definitions"):
        if td.get("code") == "at0000":
            td.find(O + "items").text = "<name>"
    for tag in ("uid", "template_id", "definition/" + O + "template_id"):
        root.find(O + tag).find(O + "value").text = "<id>"
    root.find(O + "concept").text = "<name>"
    for od in root.find(O + "description"):
        if od.get("id") in ("date", "build_uid"):
            od.text = "<x>"
    return ET.tostring(root)


@pytest.mark.parametrize("opt", ["ANTHRO_OPT", "LUNG_OPT", "LAB_OPT"])
def test_same_skeleton_as_vitals_only_content_differs(opt):
    assert _skeleton(globals()[opt]) == _skeleton(VITALS_OPT)


def test_archetype_roots_sit_where_vitals_puts_them(anthro_root):
    vit = ET.parse(VITALS_OPT).getroot()

    def shape(root):
        return [(r.findtext(O + "rm_type_name"), r.findtext(O + "node_id"),
                 r.find(O + "occurrences").findtext(O + "lower"), r.find(O + "occurrences").findtext(O + "upper"),
                 [c.tag for c in r if c.tag in (O + "archetype_id", O + "term_definitions", O + "term_bindings")][:1])
                for r in _roots(root)]
    assert set(map(str, shape(anthro_root))) == set(map(str, shape(vit)))


# --- every at-code referenced exists in its archetype's ontology ------------
def _codes_used(node):
    """at-codes used by ``node``'s own archetype: descent stops at nested roots
    (a filled slot's archetype is checked against its own ontology)."""
    used = set()

    def walk(el, top):
        if not top and el.get(XSI) == "C_ARCHETYPE_ROOT":
            return
        if el.tag == O + "node_id" and el.text:
            used.add(el.text)
        if el.tag == O + "code_list" and (el.text or "").startswith("at"):
            used.add(el.text)
        for c in el:
            walk(c, False)
    walk(node, True)
    return used


@pytest.mark.parametrize("opt", [ANTHRO_OPT, VITALS_OPT, LUNG_OPT, LAB_OPT])
def test_every_at_code_exists_in_its_archetype_ontology(opt):
    root = ET.parse(opt).getroot()
    for r in _roots(root):
        aid = r.findtext(O + "archetype_id/" + O + "value")
        arch = bo.load_archetype(aid)
        defined = {t.get("code") for t in r.findall(O + "term_definitions")}
        assert _codes_used(r) <= defined, aid
        assert {c for c in defined if c.startswith("at")} == arch.term_codes("en"), aid
    comp = root.find(O + "definition")
    defined = {t.get("code") for t in comp.findall(O + "term_definitions")}
    assert _codes_used(comp) | {"at0000"} <= defined


# --- FLAT paths --------------------------------------------------------------
def test_web_template_id_rule_matches_the_captured_vitals_web_template():
    wt = json.load(open(os.path.join(T, "pdhc_vitals.v1.webtemplate.json")))
    checked = []

    def walk(n):
        if (n.get("nodeId") or "").startswith("at") or n.get("rmType") in ("COMPOSITION", "OBSERVATION"):
            checked.append((n["name"], n["id"]))
        for c in n.get("children", []):
            walk(c)
    walk(wt["tree"])
    assert len(checked) >= 70
    assert [(nm, bo.web_template_id(nm)) for nm, _ in checked] == checked


def test_predictor_reproduces_the_proven_vitals_paths():
    src = bo.load_source(VITALS_SRC)
    src["content"] = [
        {"archetype_id": "openEHR-EHR-OBSERVATION.blood_pressure.v2",
         "keep": ["/data[at0001]/events[at0006]/data[at0003]/items[at0004]"]},
        {"archetype_id": "openEHR-EHR-OBSERVATION.pulse.v2",
         "keep": ["/data[at0002]/events[at0003]/data[at0001]/items[at0004]"]},
        {"archetype_id": "openEHR-EHR-OBSERVATION.body_temperature.v2",
         "keep": ["/data[at0002]/events[at0003]/data[at0001]/items[at0004]"]},
    ]
    got = [l["magnitude"] for l in bo.predict_flat_paths(src)["leaves"]]
    sample = json.load(open(os.path.join(T, "samples", "pdhc_vitals_roundtrip.flat.json")))
    assert got == ["pdhc_vital_signs/blood_pressure/any_event/systolic|magnitude",
                   "pdhc_vital_signs/pulse_heart_beat/any_event/rate|magnitude",
                   "pdhc_vital_signs/body_temperature/any_event/temperature|magnitude"]
    assert set(got) <= set(sample)


def test_expected_paths_file_is_in_step_with_the_generator():
    exp = json.load(open(EXPECTED))
    pred = bo.predict_flat_paths(bo.load_source(ANTHRO_SRC))
    assert exp["flat_root"] == pred["flat_root"] == "pdhc_anthropometry"
    strip = [{k: v for k, v in l.items() if k not in ("concepts", "value_kind")} for l in exp["leaves"]]
    assert strip == pred["leaves"]
    sample = json.load(open(os.path.join(T, "samples", "pdhc_vitals_roundtrip.flat.json")))
    vit_comp = sorted(k.split("/", 1)[1] for k in sample if k.count("/") <= 2 and "any_event" not in k)
    assert sorted(k.split("/", 1)[1] for k in exp["composition_keys"]) == vit_comp
    names = sorted(c["concept_name"] for l in exp["leaves"] for c in l["concepts"])
    assert names == sorted(["weight", "Self reported body Weight", "height", "bmi", "waist_circumference"])


# --- provenance --------------------------------------------------------------
def test_vendored_archetypes_match_their_provenance():
    prov = json.load(open(os.path.join(SRC, "archetypes", "PROVENANCE.json")))
    listed = set()
    for a in prov["archetypes"]:
        for d in a["downloads"].values():
            p = os.path.join(SRC, "archetypes", d["file"])
            assert hashlib.sha256(_read(p)).hexdigest() == d["sha256"], p
            assert d["pinned_url"].endswith("?asset-version=%d" % a["ckm_asset_version"])
            listed.add(d["file"])
    on_disk = {os.path.basename(p) for ext in ("xml", "adl")
               for p in glob.glob(os.path.join(SRC, "archetypes", "*." + ext))}
    assert on_disk == listed


def test_vendored_xsds_match_their_provenance():
    prov = json.load(open(os.path.join(SRC, "xsd", "PROVENANCE.json")))
    for f in prov["files"]:
        assert hashlib.sha256(_read(os.path.join(SRC, "xsd", f["file"]))).hexdigest() == f["sha256"]


# =============================================================================
# #728 follow-up: pdhc_lung_function.v1 and pdhc_laboratory.v1
# =============================================================================
LUNG_EXP = os.path.join(T, "pdhc_lung_function.v1.expected_paths.json")
LAB_EXP = os.path.join(T, "pdhc_laboratory.v1.expected_paths.json")
LUNG_SAMPLE = os.path.join(T, "samples", "pdhc_lung_function.v1_candidate.flat.json")
LAB_SAMPLE = os.path.join(T, "samples", "pdhc_laboratory.v1_candidate.flat.json")
LAB_UNITS = ["mmol/L", "mmol/mol", "%", "1", "umol/L", "mL/min/{1.73_m2}", "mg/mmol",
             "[IU]/L", "m[IU]/L", "pmol/L", "g/L", "10*9/L"]


def _all_bindings(template_id):
    """Concept bindings for a template wherever the concept map keeps them (pending
    gaps use target_template/target_archetype, live concepts template_id/archetype)."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            if template_id in (o.get("target_template"), o.get("template_id")) and "ucum" in o:
                out.append(o)
            for v in o.values():
                walk(v)
    walk(json.load(open(os.path.join(T, "pdhc_concept_map.json"))))
    return out


@pytest.mark.parametrize("src,opt", [(LUNG_SRC, LUNG_OPT), (LAB_SRC, LAB_OPT)])
def test_committed_opt_is_the_generated_one_and_deterministic(src, opt):
    data = bo.build(bo.load_source(src))
    assert data == _read(opt)
    assert bo.build(json.loads(json.dumps(bo.load_source(src), sort_keys=True))) == data


@pytest.mark.parametrize("src", [LUNG_SRC, LAB_SRC])
def test_new_templates_xsd_valid(src):
    pytest.importorskip("lxml", reason="lxml not installed in this venv; run build_opt.py --xsd with an lxml python")
    assert bo.xsd_errors(bo.build(bo.load_source(src))) == []


def test_flat_roots_follow_the_composition_name_not_the_template_id():
    for src, root in [(LUNG_SRC, "pdhc_lung_function"), (LAB_SRC, "pdhc_laboratory"),
                      (ANTHRO_SRC, "pdhc_anthropometry"), (VITALS_SRC, "pdhc_vital_signs")]:
        assert bo.predict_web_template(bo.load_source(src))["flat_root"] == root


# --- the general predictor, controlled against what the sandbox produced -----
def test_predictor_reproduces_every_leaf_of_the_captured_vitals_web_template():
    leaves = bo.predict_web_template(bo.load_source(VITALS_SRC))["leaves"]
    wt = json.load(open(os.path.join(T, "pdhc_vitals.v1.webtemplate.json")))
    captured = {}

    def walk(n, ids):
        ids = ids + [n["id"]]
        ap = n.get("aqlPath", "")
        if "/content[" in ap and (ap.endswith("/value") or ap.endswith("/time")):
            captured[ap[:-len("/value")] if ap.endswith("/value") else ap] = ("/".join(ids), n["min"], n["max"])
        for c in n.get("children", []):
            walk(c, ids)
    walk(wt["tree"], [])
    predicted = {l["aql_path"]: (l["flat_path"], l["min"], l["max"]) for l in leaves}
    assert len(captured) >= 70
    assert predicted == captured          # same leaves, ids, aqlPaths (incl. name/value splits), min, max


def test_predictor_reproduces_the_captured_anthropometry_paths():
    captured = {l.strip() for l in open(os.path.join(T, "pdhc_anthropometry.v1.sandbox_paths.txt"))
                if l.strip() and not l.startswith("#") and "|" not in l}
    leaves = bo.predict_web_template(bo.load_source(ANTHRO_SRC))["leaves"]
    assert {l["flat_path"] for l in leaves if not l["flat_path"].endswith("/time")} == captured


# --- lung function ------------------------------------------------------------
def test_lung_function_is_spirometry_v2_with_one_fev1_cluster_in_litres():
    root = ET.parse(LUNG_OPT).getroot()
    (obs,) = _roots(root)
    assert obs.findtext(O + "archetype_id/" + O + "value") == "openEHR-EHR-OBSERVATION.spirometry_result.v2"
    clusters = [c for c in obs.iter(O + "children") if c.findtext(O + "rm_type_name") == "CLUSTER"]
    assert [c.findtext(O + "node_id") for c in clusters] == ["at0052"]          # Volume
    assert clusters[0].find(O + "occurrences").findtext(O + "upper") == "1"
    assert [c.text for c in clusters[0].iter(O + "code_list")] == ["at0067"]     # name = FEV1
    elements = [c for c in obs.iter(O + "children") if c.findtext(O + "rm_type_name") == "ELEMENT"]
    assert [e.findtext(O + "node_id") for e in elements] == ["at0053"]           # Result
    (q,) = [c for c in obs.iter(O + "children") if c.get(XSI) == "C_DV_QUANTITY"]
    assert [l.findtext(O + "units") for l in q.findall(O + "list")] == ["L"]
    assert [b["ucum"] for b in _all_bindings("pdhc_lung_function.v1")] == ["L"]


def test_lung_function_expected_paths_and_sample_follow_the_predictor():
    exp = json.load(open(LUNG_EXP))
    (fev,) = [l for l in bo.predict_web_template(bo.load_source(LUNG_SRC))["leaves"]
              if l["rm_types"] == ["DV_QUANTITY"]]
    assert fev["flat_path"] == "pdhc_lung_function/spirometry_result/any_event/fev1/result"
    assert exp["leaves"][0]["flat_path"] == fev["flat_path"] and exp["leaves"][0]["allowed_units"] == ["L"]
    sample = json.load(open(LUNG_SAMPLE))
    assert sample[fev["flat_path"] + "|unit"] == "L"
    _sample_keys_are_predicted(sample, LUNG_SRC)


def test_a_name_code_the_archetype_does_not_allow_is_refused():
    src = bo.load_source(LUNG_SRC)
    c = src["content"][0]["constrain"]
    name = [p for p in c if p.endswith("/name")][0]
    c[name]["codes"] = ["at0081"]           # FEV1/FVC ratio is not a Volume name
    with pytest.raises(bo.BuildError, match="not allowed"):
        bo.build(src)


def test_occurrences_cannot_be_widened():
    src = bo.load_source(LUNG_SRC)
    c = src["content"][0]["constrain"]
    result = [p for p in c if p.endswith("items[at0053]")][0]
    c[result]["occurrences"] = [0, 2]           # the archetype says 0..1
    with pytest.raises(bo.BuildError, match="widen"):
        bo.build(src)


# --- laboratory -----------------------------------------------------------------
def _lab_analyte(root):
    (obs, analyte) = _roots(root)
    return obs, analyte


def test_laboratory_panel_shape():
    obs, analyte = _lab_analyte(ET.parse(LAB_OPT).getroot())
    assert obs.findtext(O + "archetype_id/" + O + "value") == "openEHR-EHR-OBSERVATION.laboratory_test_result.v1"
    assert analyte.findtext(O + "archetype_id/" + O + "value") == "openEHR-EHR-CLUSTER.laboratory_test_analyte.v1"
    # the analyte fills the 'Test result' slot and repeats
    assert analyte.find(O + "occurrences").findtext(O + "upper_unbounded") == "true"
    assert not [c for c in obs.iter(O + "children") if c.get(XSI) == "ARCHETYPE_SLOT"]
    own = [c.findtext(O + "node_id") for c in obs.iter(O + "children")
           if c.findtext(O + "rm_type_name") == "ELEMENT" and c not in list(analyte.iter())]
    assert own == ["at0005"]                                       # Test name, mandatory 1..1
    names = [c.findtext(O + "node_id") for c in analyte.iter(O + "children")
             if c.findtext(O + "rm_type_name") == "ELEMENT"]
    assert names == ["at0024", "at0001"]                           # Analyte name, Analyte result


def test_laboratory_analyte_name_is_coded_or_text_with_no_fixed_list():
    _, analyte = _lab_analyte(ET.parse(LAB_OPT).getroot())
    (name,) = [c for c in analyte.iter(O + "children") if c.findtext(O + "node_id") == "at0024"]
    kinds = [c.findtext(O + "rm_type_name") for a in name.findall(O + "attributes")
             for c in a.findall(O + "children")]
    assert kinds == ["DV_CODED_TEXT", "DV_TEXT"]
    assert not list(name.iter(O + "code_list"))


def test_laboratory_result_is_dv_quantity_with_exactly_the_concept_map_units():
    _, analyte = _lab_analyte(ET.parse(LAB_OPT).getroot())
    (q,) = [c for c in analyte.iter(O + "children") if c.get(XSI) == "C_DV_QUANTITY"]
    units = [l.findtext(O + "units") for l in q.findall(O + "list")]
    assert units == LAB_UNITS
    assert q.find(O + "property") is None          # the units span several properties
    assert set(units) == {b["ucum"] for b in _all_bindings("pdhc_laboratory.v1")}


def test_a_filling_archetype_must_match_the_slot():
    src = bo.load_source(LAB_SRC)
    slot = list(src["content"][0]["slots"])[0]
    src["content"][0]["slots"][slot][0]["archetype_id"] = "openEHR-EHR-OBSERVATION.body_weight.v2"
    with pytest.raises(bo.BuildError, match="does not match the slot"):
        bo.build(src)


def test_a_filled_slot_must_be_kept():
    src = bo.load_source(LAB_SRC)
    src["content"][0]["keep"] = src["content"][0]["keep"][:1]
    with pytest.raises(bo.BuildError, match="must be in keep"):
        bo.build(src)


def test_laboratory_expected_paths_and_sample_follow_the_predictor():
    exp = json.load(open(LAB_EXP))
    leaves = {l["flat_path_indexed"]: l for l in bo.predict_web_template(bo.load_source(LAB_SRC))["leaves"]}
    base = "pdhc_laboratory/laboratory_test_result/any_event:N/laboratory_analyte_result:N/"
    assert leaves[base + "analyte_result"]["units"] == LAB_UNITS
    assert set(leaves[base + "analyte_name"]["rm_types"]) == {"DV_CODED_TEXT", "DV_TEXT"}
    assert leaves["pdhc_laboratory/laboratory_test_result/any_event:N/test_name"]["min"] == 1
    ra = exp["repeating_analyte"]
    assert ra["cluster"] == "pdhc_laboratory/laboratory_test_result/any_event/laboratory_analyte_result:N"
    assert ra["allowed_units"] == LAB_UNITS and exp["units_not_expressible"] == []
    sample = json.load(open(LAB_SAMPLE))
    units = {v for k, v in sample.items() if k.endswith("analyte_result|unit")}
    assert units == {"mmol/mol", "mmol/L"}
    assert sample["pdhc_laboratory/laboratory_test_result/any_event/test_name"]
    _sample_keys_are_predicted(sample, LAB_SRC)


def _sample_keys_are_predicted(sample, src):
    """Every data key of a candidate sample is a predicted leaf (indices and
    |suffixes stripped); composition keys follow the proven vitals sample."""
    pred = bo.predict_web_template(bo.load_source(src))
    root = pred["flat_root"]
    leaves = {l["flat_path"] for l in pred["leaves"]}
    vit = json.load(open(os.path.join(T, "samples", "pdhc_vitals_roundtrip.flat.json")))
    vit_comp = {k.split("/", 1)[1] for k in vit if "any_event" not in k}
    for k in sample:
        rel = k.split("/", 1)[1]
        if rel in vit_comp:
            continue
        assert k.startswith(root + "/")
        assert re.sub(r":\d+", "", k.split("|")[0]) in leaves, k


def test_new_archetypes_are_in_the_provenance_with_a_selection_note():
    prov = json.load(open(os.path.join(SRC, "archetypes", "PROVENANCE.json")))
    by = {a["archetype_id"]: a for a in prov["archetypes"]}
    for aid in ("openEHR-EHR-OBSERVATION.spirometry_result.v2",
                "openEHR-EHR-OBSERVATION.laboratory_test_result.v1",
                "openEHR-EHR-CLUSTER.laboratory_test_analyte.v1"):
        assert by[aid]["ckm_status"] == "PUBLISHED"
        assert aid in prov["selection_notes"]
