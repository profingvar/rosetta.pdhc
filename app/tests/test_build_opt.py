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
    pending = json.load(open(os.path.join(T, "pdhc_concept_map.json")))["gaps"]["pending_template_extension"]
    src = bo.load_source(ANTHRO_SRC)
    by_arch = {i["archetype_id"]: list(i["constrain"].values())[0]["units"] for i in src["content"]}
    anthro = [v for v in pending.values()
              if isinstance(v, dict) and v.get("target_template") == "pdhc_anthropometry.v1"]
    assert len(anthro) == 5
    for v in anthro:
        assert by_arch[v["target_archetype"]] == [v["ucum"]]


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


def test_same_skeleton_as_vitals_only_content_differs():
    assert _skeleton(ANTHRO_OPT) == _skeleton(VITALS_OPT)


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
    used = set()
    for el in node.iter():
        if el.tag == O + "node_id" and el.text:
            used.add(el.text)
        if el.tag == O + "code_list" and (el.text or "").startswith("at"):
            used.add(el.text)
    return used


@pytest.mark.parametrize("opt", [ANTHRO_OPT, VITALS_OPT])
def test_every_at_code_exists_in_its_archetype_ontology(opt):
    root = ET.parse(opt).getroot()
    for r in _roots(root):
        aid = r.findtext(O + "archetype_id/" + O + "value")
        arch = bo.load_archetype(aid)
        defined = {t.get("code") for t in r.findall(O + "term_definitions")}
        assert _codes_used(r) <= defined, aid
        assert {c for c in defined if c.startswith("at")} == arch.term_codes("en"), aid
    comp = root.find(O + "definition")
    comp_used = {t for t in _codes_used(comp) if t}
    for r in _roots(root):
        comp_used -= _codes_used(r)
    comp_used.add("at0000")
    defined = {t.get("code") for t in comp.findall(O + "term_definitions")}
    assert comp_used <= defined


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
