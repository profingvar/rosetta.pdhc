#!/usr/bin/env python3
"""Generate an openEHR operational template (OPT 1.4 XML) from CKM archetypes — #728.

Operator decision 2026-11-17 (#728, authoring path B): the ``.opt`` files are
GENERATED from public CKM archetypes rather than hand-authored in Archetype
Designer, and then validated by upload to the sandbox (done by the lead).

The generator composes an OPT from

* a **template source** — small JSON data under ``templates/src/`` naming the
  template identity, the composition archetype, and the OBSERVATION archetypes
  with per-element constraints (which elements are kept, which units allowed);
* **vendored CKM archetypes** under ``templates/src/archetypes/`` — the CKM XML
  export (definition + ontology) and the CKM ADL export (only for the original
  term-binding strings, which the XML export loses, e.g. ``[SNOMED-CT(2003)::..]``).
  Their CKM CIDs, revisions, download URLs and sha256 are in ``PROVENANCE.json``.

The output reproduces the dialect of the OPT that the sandbox has ACCEPTED,
``templates/pdhc_vitals.v1.opt`` (an Archetype Designer export). The rules
below were derived by diffing that file against the CKM sources of its four
archetypes; each is applied mechanically and the vitals OPT is regenerated
from ``templates/src/pdhc_vitals.v1.json`` as the control (see the tests):

1.  The archetype root becomes ``children xsi:type="C_ARCHETYPE_ROOT"`` with the
    template's occurrences, followed by ``archetype_id``, the template-language
    ``term_definitions`` and the ``term_bindings``.
2.  Every ``attributes`` carries ``<match_negated>false</match_negated>`` after
    ``existence``.
3.  An interval with an unbounded upper limit gets ``upper_included=false``.
4.  ``ARCHETYPE_INTERNAL_REF`` is expanded into a copy of its target.
5.  ``C_PRIMITIVE_OBJECT.rm_type_name`` is the primitive's type (``DURATION``,
    not CKM's ``DV_DURATION``); a ``C_DURATION`` given as a single ``value`` is
    written as the closed range [value, value].
6.  ``EXPR_LEAF`` constraint operands of type ``C_STRING`` are typed ``String``;
    the ``xsd:`` prefix is written ``xs:``.
7.  ``term_definitions``/``term_bindings`` keep the archetype's order except that
    ``at0000`` goes last; unit display terms (template data) follow, sorted.
8.  The composition root's ``at0000`` text is the template's composition name —
    this is what becomes the web template / FLAT root id.
9.  Serialisation: 4-space indent, ``<x></x>`` for empty elements, the ``xsi`` /
    ``xs`` namespaces declared on the first element that needs them.

Template constraints applied on top (none for vitals, which inlines the
archetypes whole):

* ``keep`` — archetype paths of the nodes to keep. Everything not on a path to a
  kept node (and not inside one) is removed, except attributes the archetype
  makes mandatory (existence lower >= 1). Removing a mandatory object is an error.
* ``constrain[path].units`` — narrows a ``C_DV_QUANTITY`` unit list. Each unit
  must already be allowed by the archetype (a template can only narrow). An
  ELEMENT whose value the archetype leaves unconstrained (any data type) is
  narrowed to a DV_QUANTITY with exactly those units.
* ``constrain[path].codes`` — narrows a local code list (e.g. a cluster's coded
  ``name``: a spirometry Volume cluster fixed to FEV1).
* ``constrain[path].occurrences`` — narrows a node's occurrences; never widens.
* ``constrain[path].allow_coded_text`` — adds an open DV_CODED_TEXT alternative
  before a DV_TEXT value (the coded-or-text choice CKM itself uses).
* ``slots{slot path: [archetype spec, ...]}`` — fills an ARCHETYPE_SLOT with
  nested C_ARCHETYPE_ROOTs (each built by the same rules, own ontology); the
  archetype id must match the slot's include pattern and the slot must be kept.

``predict_web_template()`` walks the built template the way EHRbase builds a web
template and lists every data leaf with its predicted FLAT path. Its control is
the captured vitals web template: all 73 leaves, ids, aqlPaths, min and max
match, both ways (see the tests).

Pure stdlib, deterministic: the same inputs give the same bytes.

Usage::

    python scripts/build_opt.py templates/src/pdhc_anthropometry.v1.json \\
        -o templates/pdhc_anthropometry.v1.opt
    python scripts/build_opt.py templates/src/pdhc_vitals.v1.json --check templates/pdhc_vitals.v1.opt
    python scripts/build_opt.py templates/src/pdhc_anthropometry.v1.json --paths
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET

OPENEHR_NS = "http://schemas.openehr.org/v1"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XS_NS = "http://www.w3.org/2001/XMLSchema"
_O = "{%s}" % OPENEHR_NS
_XSI_TYPE = "{%s}type" % XSI_NS

ROSETTA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHETYPE_DIR = os.path.join(ROSETTA_ROOT, "templates", "src", "archetypes")
XSD_DIR = os.path.join(ROSETTA_ROOT, "templates", "src", "xsd")


class BuildError(ValueError):
    """The template source asks for something the archetypes do not allow."""


# ---------------------------------------------------------------------------
# A minimal ordered XML node — full control over the serialised bytes.
# ---------------------------------------------------------------------------
class Node:
    __slots__ = ("tag", "attrs", "text", "children")

    def __init__(self, tag, attrs=None, text=None, children=None):
        self.tag = tag
        self.attrs = list(attrs or [])        # [(name, value)], e.g. ("xsi:type", "C_STRING")
        self.text = text
        self.children = list(children or [])

    # -- helpers ---------------------------------------------------------
    def get(self, name):
        for k, v in self.attrs:
            if k == name:
                return v
        return None

    def set(self, name, value):
        for i, (k, _) in enumerate(self.attrs):
            if k == name:
                self.attrs[i] = (name, value)
                return
        self.attrs.append((name, value))

    def find(self, tag):
        for c in self.children:
            if c.tag == tag:
                return c
        return None

    def findall(self, tag):
        return [c for c in self.children if c.tag == tag]

    def findtext(self, tag, default=None):
        c = self.find(tag)
        return default if c is None else (c.text or "")

    def iter_nodes(self):
        yield self
        for c in self.children:
            yield from c.iter_nodes()

    def index(self, tag):
        for i, c in enumerate(self.children):
            if c.tag == tag:
                return i
        return -1


def leaf(tag, text):
    return Node(tag, text=text)


def from_etree(e) -> Node:
    tag = e.tag.split("}", 1)[1] if e.tag.startswith("{") else e.tag
    attrs = []
    for k, v in e.attrib.items():
        if k == _XSI_TYPE:
            if v.startswith("xsd:"):          # rule 6: CKM writes xsd:, the OPT xs:
                v = "xs:" + v[4:]
            attrs.append(("xsi:type", v))
        else:
            attrs.append((k.split("}", 1)[-1], v))
    kids = [from_etree(c) for c in e]
    return Node(tag, attrs, None if kids else (e.text or ""), kids)


# ---------------------------------------------------------------------------
# Serialisation (rule 9)
# ---------------------------------------------------------------------------
def _esc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(s):
    return _esc_text(s).replace('"', "&quot;")


def serialise(root: Node) -> bytes:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?> \n']

    def needs(node):
        need = []
        for k, v in node.attrs:
            if k.startswith("xsi:"):
                need.append("xsi")
            if k == "xsi:type" and v.startswith("xs:"):
                need.append("xs")
        return need

    def emit(node, depth, declared):
        pad = "    " * depth
        parts = [node.tag]
        parts += ['%s="%s"' % (k, _esc_attr(v)) for k, v in node.attrs]
        declared = set(declared)
        for p in needs(node):
            if p not in declared:
                parts.append('xmlns:%s="%s"' % (p, XSI_NS if p == "xsi" else XS_NS))
                declared.add(p)
        open_tag = "<" + " ".join(parts) + ">"
        if node.children:
            out.append(pad + open_tag + "\n")
            for c in node.children:
                emit(c, depth + 1, declared)
            out.append(pad + "</%s>\n" % node.tag)
        else:
            out.append(pad + open_tag + _esc_text(node.text or "") + "</%s>\n" % node.tag)

    root = copy.copy(root)
    root.attrs = [("xmlns", OPENEHR_NS)] + list(root.attrs)
    emit(root, 0, set())
    return "".join(out).encode("utf-8")


# ---------------------------------------------------------------------------
# Archetype loading
# ---------------------------------------------------------------------------
_ADL_BINDING_BLOCK = re.compile(r"^\tterm_bindings = <\n(.*?)^\t>", re.S | re.M)
_ADL_TERMINOLOGY = re.compile(r'^\t\t\["([^"]+)"\] = <\n\t\t\titems = <\n(.*?)^\t\t\t>', re.S | re.M)
_ADL_ITEM = re.compile(r'^\t\t\t\t\["([^"]+)"\] = <\[(.*?)\]>', re.M)


_ADL_TERMDEF_BLOCK = re.compile(r"^\tterm_definitions = <\n(.*?)^\t>", re.S | re.M)
_ADL_LANGUAGE = re.compile(r'^\t\t\["([^"]+)"\] = <\n\t\t\titems = <\n(.*?)^\t\t\t>', re.S | re.M)
_ADL_TERM = re.compile(r'^\t\t\t\t\["([^"]+)"\] = <\n(.*?)^\t\t\t\t>', re.S | re.M)
_ADL_FIELD = re.compile(r'^\t\t\t\t\t(\w+) = <"((?:[^"\\]|\\.)*)">', re.S | re.M)


def _adl_unescape(s):
    return re.sub(r"\\(.)", r"\1", s)


def adl_term_definitions(adl_text: str):
    """{language: [(code, [(id, text), ...]), ...]} in ADL order.

    The texts come from the ADL, not the CKM XML export: the XML export trims
    trailing whitespace (blood_pressure at0013's comment ends in a space in the
    ADL and in the accepted vitals OPT, not in the XML)."""
    m = _ADL_TERMDEF_BLOCK.search(adl_text)
    out = {}
    for lang in _ADL_LANGUAGE.finditer(m.group(1)):
        out[lang.group(1)] = [
            (t.group(1), [(f.group(1), _adl_unescape(f.group(2))) for f in _ADL_FIELD.finditer(t.group(2))])
            for t in _ADL_TERM.finditer(lang.group(2))]
    return out


def _read_adl(path):
    # utf-8-sig drops CKM's BOM; text mode turns CKM's CRLF into LF (as an XML parser would)
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def adl_term_bindings(adl_text: str):
    """[(terminology, [(code, '[TERM::code]'), ...]), ...] in ADL order."""
    m = _ADL_BINDING_BLOCK.search(adl_text)
    if not m:
        return []
    out = []
    for t in _ADL_TERMINOLOGY.finditer(m.group(1)):
        out.append((t.group(1), [(c, "[%s]" % v) for c, v in _ADL_ITEM.findall(t.group(2))]))
    return out


class Archetype:
    def __init__(self, archetype_id, xml_path, adl_path):
        self.archetype_id = archetype_id
        root = ET.parse(xml_path).getroot()
        aid = root.find(_O + "archetype_id/" + _O + "value").text
        if aid != archetype_id:
            raise BuildError("%s holds %s, not %s" % (xml_path, aid, archetype_id))
        self.definition = from_etree(root.find(_O + "definition"))
        adl = _read_adl(adl_path)
        if not re.search(r"^\t%s$" % re.escape(archetype_id), adl, re.M):
            raise BuildError("%s is not the ADL of %s" % (adl_path, archetype_id))
        self.terms = adl_term_definitions(adl)     # language -> [(code, [(id, text)])]
        self.bindings = adl_term_bindings(adl)
        # cross-check the ADL ontology against the XML one: same languages, codes, item ids
        ws = lambda s: " ".join((s or "").split())    # noqa: E731
        xml_terms = {td.get("language"): [(it.get("code"), [(i.get("id"), ws(i.text)) for i in it])
                                          for it in td]
                     for td in root.find(_O + "ontology").findall(_O + "term_definitions")}
        adl_terms = {lang: [(c, [(i, ws(t)) for i, t in items]) for c, items in v]
                     for lang, v in self.terms.items()}
        if xml_terms != adl_terms:
            raise BuildError("%s: XML and ADL term definitions disagree" % archetype_id)
        xml_b = [(tb.get("terminology"), [it.get("code") for it in tb])
                 for tb in root.find(_O + "ontology").findall(_O + "term_bindings")]
        adl_b = [(t, [c for c, _ in items]) for t, items in self.bindings]
        if sorted(xml_b) != sorted(adl_b):
            raise BuildError("%s: XML and ADL term bindings disagree" % archetype_id)

    def term_codes(self, language):
        return {c for c, _ in self.terms[language]}


def load_archetype(archetype_id, archetype_dir=ARCHETYPE_DIR) -> Archetype:
    base = os.path.join(archetype_dir, archetype_id)
    return Archetype(archetype_id, base + ".xml", base + ".adl")


# ---------------------------------------------------------------------------
# Definition transforms (rules 2-6) and paths
# ---------------------------------------------------------------------------
def _parse_path(path):
    segs = []
    for s in path.strip("/").split("/"):
        m = re.fullmatch(r"([a-z_]+)(?:\[([^\]]+)\])?", s)
        if not m:
            raise BuildError("bad archetype path segment %r in %r" % (s, path))
        segs.append((m.group(1), m.group(2)))
    return segs


def resolve(root: Node, path: str) -> Node:
    node = root
    for attr, nid in _parse_path(path):
        a = next((x for x in node.findall("attributes")
                  if x.findtext("rm_attribute_name") == attr), None)
        if a is None:
            raise BuildError("path %s: no attribute %r" % (path, attr))
        kids = a.findall("children")
        if nid is None:
            if len(kids) != 1:
                raise BuildError("path %s: %r needs a node id" % (path, attr))
            node = kids[0]
        else:
            node = next((k for k in kids if k.findtext("node_id") == nid), None)
            if node is None:
                raise BuildError("path %s: no %s[%s]" % (path, attr, nid))
    return node


def expand_internal_refs(root: Node):
    """Rule 4: replace each ARCHETYPE_INTERNAL_REF by a copy of its target."""
    def walk(node):
        for a in node.findall("attributes"):
            for i, c in enumerate(a.children):
                if c.tag == "children" and c.get("xsi:type") == "ARCHETYPE_INTERNAL_REF":
                    target = copy.deepcopy(resolve(root, c.findtext("target_path")))
                    occ = c.find("occurrences")
                    if occ is not None:
                        target.children[target.index("occurrences")] = copy.deepcopy(occ)
                    a.children[i] = target
                    walk(target)
                elif c.tag == "children":
                    walk(c)
    walk(root)


_INTERVAL_TAGS = {"occurrences", "existence", "interval", "range", "magnitude", "precision"}


def normalise(node: Node):
    """Rules 2, 3, 5, 6 — the Archetype Designer OPT dialect."""
    # rule 3
    if node.find("upper_unbounded") is not None and node.find("upper_included") is None:
        li = node.index("lower_included")
        node.children.insert(li + 1 if li >= 0 else 0, leaf("upper_included", "false"))
    if node.tag == "attributes" and node.find("match_negated") is None:   # rule 2
        node.children.insert(node.index("existence") + 1, leaf("match_negated", "false"))
    if node.get("xsi:type") == "C_PRIMITIVE_OBJECT":                     # rule 5
        item = node.find("item")
        prim = item.get("xsi:type")
        node.find("rm_type_name").text = prim[2:] if prim.startswith("C_") else prim
        if prim == "C_DURATION" and item.find("value") is not None and len(item.children) == 1:
            v = item.find("value").text
            item.children = [Node("range", children=[
                leaf("lower_included", "true"), leaf("upper_included", "true"),
                leaf("lower_unbounded", "false"), leaf("upper_unbounded", "false"),
                leaf("lower", v), leaf("upper", v)])]
    if node.get("xsi:type") == "EXPR_LEAF" and node.findtext("type") == "C_STRING":  # rule 6
        node.find("type").text = "String"
    for c in node.children:
        normalise(c)


def _occ_lower(node, tag="occurrences"):
    o = node.find(tag)
    return int(o.findtext("lower", "0")) if o is not None else 0


def prune(root: Node, keep_paths):
    """Keep only the nodes on (or inside) ``keep_paths``; see module docstring."""
    targets = [_parse_path(p) for p in keep_paths]

    def walk(node, prefix):
        for a in list(node.findall("attributes")):
            name = a.findtext("rm_attribute_name")
            on_path = [t for t in targets if len(t) > len(prefix) and t[len(prefix)][0] == name
                       and all(t[i][0] == prefix[i][0] and t[i][1] == prefix[i][1]
                               for i in range(len(prefix)))]
            if not on_path:
                if _occ_lower(a, "existence") >= 1:
                    continue            # mandatory attribute: keep whole
                node.children.remove(a)
                continue
            for c in list(a.findall("children")):
                nid = c.findtext("node_id") or None
                matching = [t for t in on_path if t[len(prefix)][1] in (nid, None)]
                if not matching:
                    # #728 M2: the children of a SINGLE attribute are
                    # alternatives (an ACTION's ism_transition: exactly one of
                    # them is present), so choosing some of them is a template
                    # narrowing, not the removal of a mandatory node -- as long
                    # as at least one alternative stays (it does: it is on a
                    # keep path).
                    single = (a.get("xsi:type") or "") == "C_SINGLE_ATTRIBUTE"
                    if _occ_lower(c) >= 1 and not single:
                        raise BuildError("cannot remove mandatory %s[%s]" % (name, nid))
                    a.children.remove(c)
                    continue
                if any(len(t) == len(prefix) + 1 for t in matching):
                    continue            # a kept target: keep its whole subtree
                walk(c, prefix + [(name, nid)])
    walk(root, [])
    for p in keep_paths:       # every keep path must still resolve
        resolve(root, p)


def _value_attr(el: Node):
    return next((a for a in el.findall("attributes") if a.findtext("rm_attribute_name") == "value"), None)


def constrain_units(root: Node, element_path: str, units):
    """Narrow an ELEMENT's DV_QUANTITY units. If the archetype leaves the value
    unconstrained (any data type, e.g. laboratory_test_analyte 'Analyte result'),
    narrow it to a DV_QUANTITY carrying exactly ``units`` (no property: the
    units may span several properties)."""
    el = resolve(root, element_path)
    va = _value_attr(el)
    if va is None:
        if el.findtext("rm_type_name") != "ELEMENT":
            raise BuildError("%s: units apply to an ELEMENT" % element_path)
        q = Node("children", [("xsi:type", "C_DV_QUANTITY")], children=[
            leaf("rm_type_name", "DV_QUANTITY"), _interval("occurrences", 1, 1), leaf("node_id", "")]
            + [Node("list", children=[leaf("units", u)]) for u in units])
        el.children.append(Node("attributes", [("xsi:type", "C_SINGLE_ATTRIBUTE")], children=[
            leaf("rm_attribute_name", "value"), _interval("existence", 0, 1), q]))
        return
    q = [c for c in va.findall("children") if c.get("xsi:type") == "C_DV_QUANTITY"]
    if len(q) != 1:
        raise BuildError("%s: expected one C_DV_QUANTITY value, found %d" % (element_path, len(q)))
    q = q[0]
    lists = q.findall("list")
    allowed = [l.findtext("units") for l in lists]
    for u in units:
        if u not in allowed:
            raise BuildError("%s: unit %r not allowed by the archetype (%s)"
                             % (element_path, u, ", ".join(allowed)))
    by_unit = {l.findtext("units"): l for l in lists}
    first = q.index("list")
    rest = [c for c in q.children if c.tag != "list"]
    q.children = rest[:first] + [by_unit[u] for u in units] + rest[first:]


def constrain_codes(root: Node, path: str, codes):
    """Narrow the local code list of the DV_CODED_TEXT at ``path`` (e.g. a cluster's
    coded ``name``) to ``codes``, which must all be allowed by the archetype."""
    target = resolve(root, path)
    cps = [c for a in target.findall("attributes") if a.findtext("rm_attribute_name") == "defining_code"
           for c in a.findall("children") if c.get("xsi:type") == "C_CODE_PHRASE"]
    if len(cps) != 1:
        raise BuildError("%s: expected one C_CODE_PHRASE, found %d" % (path, len(cps)))
    cp = cps[0]
    allowed = [c.text for c in cp.findall("code_list")]
    bad = [c for c in codes if c not in allowed]
    if bad:
        raise BuildError("%s: codes %s not allowed by the archetype" % (path, bad))
    first = cp.index("code_list")
    rest = [c for c in cp.children if c.tag != "code_list"]
    cp.children = rest[:first] + [leaf("code_list", c) for c in codes] + rest[first:]


def constrain_occurrences(root: Node, path: str, lower, upper):
    node = resolve(root, path)
    o = node.find("occurrences")
    alo = int(o.findtext("lower", "0"))
    aup = None if o.findtext("upper_unbounded") == "true" else int(o.findtext("upper"))
    if lower < alo or (aup is not None and (upper is None or upper > aup)):
        raise BuildError("%s: occurrences %s..%s widen the archetype's %s..%s"
                         % (path, lower, upper, alo, aup))
    _set_occurrences(node, lower, upper)


def allow_coded_text(root: Node, element_path: str):
    """Add an open DV_CODED_TEXT alternative before an ELEMENT's DV_TEXT value
    (DV_CODED_TEXT is a DV_TEXT, so this narrows nothing away; it is the
    'coded or text' choice CKM itself writes, e.g. blood_pressure at0014)."""
    va = _value_attr(resolve(root, element_path))
    kids = va.findall("children") if va is not None else []
    if [k.findtext("rm_type_name") for k in kids] != ["DV_TEXT"]:
        raise BuildError("%s: allow_coded_text needs a DV_TEXT-only value" % element_path)
    i = va.children.index(kids[0])
    va.children.insert(i, Node("children", [("xsi:type", "C_COMPLEX_OBJECT")], children=[
        leaf("rm_type_name", "DV_CODED_TEXT"), _interval("occurrences", 1, 1), leaf("node_id", "")]))


def apply_constraints(root: Node, constrain: dict):
    for path, c in sorted((constrain or {}).items()):
        unknown = set(c) - {"units", "codes", "occurrences", "allow_coded_text"}
        if unknown:
            raise BuildError("%s: unknown constraint(s) %s" % (path, sorted(unknown)))
        if "units" in c:
            constrain_units(root, path, c["units"])
        if "codes" in c:
            constrain_codes(root, path, c["codes"])
        if "occurrences" in c:
            constrain_occurrences(root, path, *c["occurrences"])
        if c.get("allow_coded_text"):
            allow_coded_text(root, path)


def slot_include_patterns(slot: Node):
    """The archetype-id regexes of an ARCHETYPE_SLOT's ``includes``."""
    return [n.findtext("pattern") for inc in slot.findall("includes")
            for n in inc.iter_nodes() if n.tag == "item" and n.get("xsi:type") == "C_STRING"]


def quantity_units(node: Node, skip_roots=False):
    """Units of every C_DV_QUANTITY below ``node`` (not inside nested archetype
    roots when ``skip_roots``: those carry their own unit terms)."""
    out = set()
    if node.get("xsi:type") == "C_DV_QUANTITY":
        out |= {l.findtext("units") for l in node.findall("list")}
    for c in node.children:
        if skip_roots and c.get("xsi:type") == "C_ARCHETYPE_ROOT":
            continue
        out |= quantity_units(c, skip_roots)
    return out


# ---------------------------------------------------------------------------
# Ontology (rule 7)
# ---------------------------------------------------------------------------
def _at0000_last(items, key=lambda x: x[0]):
    return [x for x in items if key(x) != "at0000"] + [x for x in items if key(x) == "at0000"]


def term_definition_nodes(arch: Archetype, language, overrides=None, unit_terms=None,
                          units_present=()):
    overrides = overrides or {}
    codes = {c for c, _ in arch.terms[language]}
    for code, items in overrides.items():
        if code not in codes:
            raise BuildError("%s: term override for unknown code %s" % (arch.archetype_id, code))
        known = {i for c, its in arch.terms[language] if c == code for i, _ in its}
        if not set(items) <= known:
            raise BuildError("%s: term override %s names unknown items %s"
                             % (arch.archetype_id, code, sorted(set(items) - known)))
    out = []
    for code, items in _at0000_last(arch.terms[language]):
        kids = []
        for iid, text in items:
            text = overrides.get(code, {}).get(iid, text)
            kids.append(Node("items", [("id", iid)], text))
        out.append(Node("term_definitions", [("code", code)], children=kids))
    for u in sorted(units_present):
        if unit_terms and u in unit_terms:
            t = unit_terms[u]
            out.append(Node("term_definitions", [("code", u)], children=[
                Node("items", [("id", "text")], t["text"]),
                Node("items", [("id", "description")], t["description"])]))
    return out


def term_binding_nodes(arch: Archetype):
    out = []
    for terminology, items in arch.bindings:
        kids = []
        for code, code_string in _at0000_last(items):
            # terminology id = the code string's terminology without its version
            term_id = code_string[1:].split("::", 1)[0].split("(", 1)[0]
            kids.append(Node("items", [("code", code)], children=[Node("value", children=[
                Node("terminology_id", children=[leaf("value", term_id)]),
                leaf("code_string", code_string)])]))
        out.append(Node("term_bindings", [("terminology", terminology)], children=kids))
    return out


# ---------------------------------------------------------------------------
# Template assembly
# ---------------------------------------------------------------------------
def _interval(tag, lower, upper):
    kids = [leaf("lower_included", "true"),
            leaf("upper_included", "true" if upper is not None else "false"),
            leaf("lower_unbounded", "false"),
            leaf("upper_unbounded", "false" if upper is not None else "true"),
            leaf("lower", str(lower))]
    if upper is not None:
        kids.append(leaf("upper", str(upper)))
    return Node(tag, children=kids)


def _set_occurrences(node: Node, lower, upper):
    node.children[node.index("occurrences")] = _interval("occurrences", lower, upper)


def build_archetype_root(arch: Archetype, spec: dict, language, unit_terms,
                         archetype_dir=ARCHETYPE_DIR) -> Node:
    d = copy.deepcopy(arch.definition)
    expand_internal_refs(d)
    slots = spec.get("slots") or {}
    keep = spec.get("keep")
    if keep is not None:
        missing = [p for p in slots if p not in keep]
        if missing:
            raise BuildError("%s: filled slots must be in keep: %s" % (arch.archetype_id, missing))
        prune(d, keep)
    apply_constraints(d, spec.get("constrain"))
    normalise(d)
    # fill slots: each ARCHETYPE_SLOT is replaced by the C_ARCHETYPE_ROOT(s) filling it
    for path in sorted(slots):
        slot = resolve(d, path)
        if slot.get("xsi:type") != "ARCHETYPE_SLOT":
            raise BuildError("%s %s is not an ARCHETYPE_SLOT" % (arch.archetype_id, path))
        pats = slot_include_patterns(slot)
        parent_attr = next(n for n in d.iter_nodes()
                           if n.tag == "attributes" and any(c is slot for c in n.children))
        filled = []
        for sub in slots[path]:
            if not any(re.fullmatch(p, sub["archetype_id"]) for p in pats):
                raise BuildError("%s does not match the slot %s includes %s"
                                 % (sub["archetype_id"], path, pats))
            sarch = load_archetype(sub["archetype_id"], archetype_dir)
            if sarch.definition.findtext("rm_type_name") != slot.findtext("rm_type_name"):
                raise BuildError("%s is not a %s" % (sub["archetype_id"], slot.findtext("rm_type_name")))
            sub = dict(sub)
            if "occurrences" not in sub:      # default: the slot's own occurrences
                o = slot.find("occurrences")
                sub["occurrences"] = [int(o.findtext("lower")),
                                      None if o.findtext("upper_unbounded") == "true"
                                      else int(o.findtext("upper"))]
            filled.append(build_archetype_root(sarch, sub, language, unit_terms, archetype_dir))
        i = next(k for k, c in enumerate(parent_attr.children) if c is slot)
        parent_attr.children[i:i + 1] = filled
    lo, up = spec.get("occurrences", [0, 1])
    _set_occurrences(d, lo, up)
    root = Node("children", [("xsi:type", "C_ARCHETYPE_ROOT")], children=d.children)
    root.children.append(Node("archetype_id", children=[leaf("value", arch.archetype_id)]))
    own = Node("x", children=[c for c in d.children if c.tag == "attributes"])
    root.children += term_definition_nodes(arch, language, spec.get("term_overrides"),
                                           unit_terms=unit_terms,
                                           units_present=quantity_units(own, skip_roots=True))
    root.children += term_binding_nodes(arch)
    return root


def build(source: dict, archetype_dir=ARCHETYPE_DIR) -> bytes:
    return serialise(build_tree(source, archetype_dir))


def build_tree(source: dict, archetype_dir=ARCHETYPE_DIR) -> Node:
    lang = source["language"]
    desc = source["description"]
    comp = source["composition"]
    unit_terms = source.get("unit_terms") or {}

    def code_phrase(tag, terminology, code):
        return Node(tag, children=[Node("terminology_id", children=[leaf("value", terminology)]),
                                   leaf("code_string", code)])

    description = Node("description", children=(
        [Node("original_author", [("id", k)], v) for k, v in desc["original_author"]]
        + [leaf("lifecycle_state", desc["lifecycle_state"])]
        + [Node("other_details", [("id", k)], v) for k, v in desc["other_details"]]
        + [Node("details", children=[code_phrase("language", "ISO_639-1", lang),
                                     leaf("purpose", desc["purpose"])])]))

    carch = load_archetype(comp["archetype_id"], archetype_dir)
    cdef = copy.deepcopy(carch.definition)
    expand_internal_refs(cdef)
    normalise(cdef)

    content = Node("attributes", [("xsi:type", "C_MULTIPLE_ATTRIBUTE")], children=[
        leaf("rm_attribute_name", "content"),
        _interval("existence", *comp["content_existence"]),
        leaf("match_negated", "false")])
    for spec in source["content"]:
        arch = load_archetype(spec["archetype_id"], archetype_dir)
        if arch.definition.findtext("rm_type_name") != spec.get("rm_type", "OBSERVATION"):
            raise BuildError("%s is not an %s" % (spec["archetype_id"], spec.get("rm_type")))
        content.children.append(build_archetype_root(arch, spec, lang, unit_terms, archetype_dir))
    card = comp["content_cardinality"]
    ci = _interval("interval", card["lower"], card.get("upper"))
    content.children.append(Node("cardinality", children=[
        leaf("is_ordered", "true" if card["ordered"] else "false"),
        leaf("is_unique", "true" if card["unique"] else "false"), ci]))
    cdef.children.append(content)
    cdef.children.append(Node("archetype_id", children=[leaf("value", carch.archetype_id)]))
    cdef.children.append(Node("template_id", children=[leaf("value", source["template_id"])]))
    cdef.children += term_definition_nodes(carch, lang, {"at0000": {"text": comp["name"]}})
    cdef.children += term_binding_nodes(carch)
    definition = Node("definition", children=cdef.children)

    template = Node("template", children=[
        code_phrase("language", "ISO_639-1", lang),
        description,
        Node("uid", children=[leaf("value", source["uid"])]),
        Node("template_id", children=[leaf("value", source["template_id"])]),
        leaf("concept", source["concept"]),
        definition,
    ])
    return template


# ---------------------------------------------------------------------------
# FLAT path prediction (web-template ids, EHRbase rule)
# ---------------------------------------------------------------------------
def web_template_id(name: str) -> str:
    """EHRbase web-template node id from a node's name: accents stripped, every
    character other than [A-Za-z0-9_ -] becomes a space, lower-cased, runs of
    space -> one '_', and an 'a' prefix if it starts with a digit. Checked
    against all 74 archetyped nodes of the captured vitals web template."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^A-Za-z0-9_ \-]", " ", s).lower()
    s = "_".join(s.split())
    return "a" + s if s[:1].isdigit() else s


def predict_flat_paths(source: dict, archetype_dir=ARCHETYPE_DIR) -> dict:
    """For every kept DV_QUANTITY leaf: its predicted FLAT keys and allowed units."""
    lang = source["language"]
    flat_root = web_template_id(source["composition"]["name"])
    out = {"template_id": source["template_id"], "flat_root": flat_root, "leaves": []}
    for spec in source["content"]:
        arch = load_archetype(spec["archetype_id"], archetype_dir)
        text = {c: dict(items).get("text") for c, items in arch.terms[lang]}
        for c, items in (spec.get("term_overrides") or {}).items():
            text[c] = items.get("text", text[c])
        for path in spec.get("keep") or []:
            segs = _parse_path(path)
            event_nid = segs[1][1]
            elem_nid = segs[-1][1]
            units = (spec.get("constrain") or {}).get(path, {}).get("units")
            obs_id = web_template_id(text["at0000"])
            ev_id = web_template_id(text[event_nid])
            el_id = web_template_id(text[elem_nid])
            leaf_path = "/".join([flat_root, obs_id, ev_id, el_id])
            out["leaves"].append({
                "archetype_id": spec["archetype_id"],
                "archetype_path": path,
                "flat_path": leaf_path,
                "magnitude": leaf_path + "|magnitude",
                "unit": leaf_path + "|unit",
                "allowed_units": units,
                "time_path": "/".join([flat_root, obs_id, ev_id, "time"]),
            })
    return out


_STRUCTURAL = {"HISTORY", "ITEM_TREE", "ITEM_LIST", "ITEM_SINGLE", "ITEM_TABLE"}
_EVENTS = {"EVENT", "POINT_EVENT", "INTERVAL_EVENT"}


def _occ(node):
    o = node.find("occurrences")
    lo = int(o.findtext("lower", "0"))
    up = -1 if o.findtext("upper_unbounded") == "true" else int(o.findtext("upper"))
    return lo, up


def _name_variants(node, terms):
    """[(name, aql name predicate)] for a node. A ``name`` constrained to local
    codes makes one web-template node PER code, named by that code's text, as
    EHRbase does (captured vitals: pulse.v2 at1023, name in {at1057, at1058} ->
    'clinical_interpretation' and 'rhythm', aqlPath [at1023 and name/value='..']).
    So a spirometry Volume cluster narrowed to the single code FEV1 is 'fev1'."""
    nid = node.findtext("node_id")
    for a in node.findall("attributes"):
        if a.findtext("rm_attribute_name") == "name":
            codes = [n.text for n in a.iter_nodes() if n.tag == "code_list"]
            if codes and all(c in terms for c in codes):
                return [(terms[c], " and name/value='%s'" % terms[c]) for c in codes]
    return [(terms[nid], "")]


def predict_web_template(source: dict, archetype_dir=ARCHETYPE_DIR) -> dict:
    """Walk the BUILT template the way EHRbase builds a web template and list its
    data leaves (every ELEMENT plus each event's ``time``) with predicted FLAT
    paths. Structural nodes (HISTORY, ITEM_TREE, ...) take no path segment; an
    archetype root is named by its at0000 text; a node whose max occurrences is
    not 1 is ``repeating`` and takes ``:N`` in the indexed path (``:0`` may be
    omitted, as the proven vitals/anthropometry FLAT samples do for any_event)."""
    tree = build_tree(source, archetype_dir)
    definition = tree.find("definition")
    flat_root = web_template_id(source["composition"]["name"])
    leaves = []

    def terms_of(root):
        return {t.get("code"): next((i.text for i in t.children if i.get("id") == "text"), None)
                for t in root.findall("term_definitions")}

    def walk(node, terms, segs, apath):
        rm = node.findtext("rm_type_name")
        if node.get("xsi:type") == "ARCHETYPE_SLOT":
            return
        if node.get("xsi:type") == "C_ARCHETYPE_ROOT":
            terms = terms_of(node)
            variants = [(terms["at0000"], "")]
            pred = node.find("archetype_id").findtext("value")
        elif not node.findtext("node_id"):
            return      # non-archetyped RM attribute value (INTERVAL_EVENT width, math_function)
        else:
            variants = _name_variants(node, terms)
            pred = node.findtext("node_id")
        for name, name_pred in variants:
            vsegs, vpath = segs, apath + "[%s%s]" % (pred, name_pred)
            if rm not in _STRUCTURAL:
                lo, up = _occ(node)
                vsegs = segs + [(web_template_id(name), lo, up)]
            if rm == "ELEMENT":
                va = next((a for a in node.findall("attributes")
                           if a.findtext("rm_attribute_name") == "value"), None)
                kids = va.findall("children") if va is not None else []
                leaves.append(_leaf(flat_root, vsegs, vpath, [k.findtext("rm_type_name") for k in kids],
                                    [l.findtext("units") for k in kids for l in k.findall("list")]))
                continue
            if rm in _EVENTS:
                leaves.append(_leaf(flat_root, vsegs + [("time", 1, 1)], vpath + "/time",
                                    ["DV_DATE_TIME"], []))
            for a in node.findall("attributes"):
                aname = a.findtext("rm_attribute_name")
                if aname == "name":
                    continue
                for c in a.findall("children"):
                    walk(c, terms, vsegs, vpath + "/" + aname)

    content = next(a for a in definition.findall("attributes")
                   if a.findtext("rm_attribute_name") == "content")
    for c in content.findall("children"):
        walk(c, None, [], "/content")
    return {"template_id": source["template_id"], "flat_root": flat_root, "leaves": leaves}


def _leaf(flat_root, segs, apath, rm_types, units):
    plain = "/".join([flat_root] + [s for s, _, _ in segs])
    indexed = "/".join([flat_root] + [s + (":N" if up != 1 else "") for s, _, up in segs])
    return {"flat_path": plain, "flat_path_indexed": indexed, "aql_path": apath,
            "rm_types": rm_types, "units": units or None,
            "min": segs[-1][1], "max": segs[-1][2]}


# ---------------------------------------------------------------------------
# XSD validation (optional: needs lxml, which rosetta's runtime does not ship)
# ---------------------------------------------------------------------------
def xsd_errors(opt_bytes: bytes, xsd_dir=XSD_DIR):
    """Validate against the openEHR OPT 1.4 schema (Template.xsd); [] if valid.

    The ITS-XML schemas target namespace .../v2, every OPT 1.4 in use is in
    .../v1, so the schemas are loaded with that one rewrite (see
    templates/src/xsd/PROVENANCE.json). Raises ImportError without lxml."""
    from lxml import etree

    class _V1(etree.Resolver):
        def resolve(self, url, pubid, context):
            path = url[len("file://"):] if url.startswith("file://") else url
            with open(path, "rb") as fh:
                return self.resolve_string(_to_v1(fh.read()), context, base_url=path)

    def _to_v1(b):
        return b.replace(b"http://schemas.openehr.org/v2", b"http://schemas.openehr.org/v1")

    parser = etree.XMLParser()
    parser.resolvers.add(_V1())
    main_xsd = os.path.join(xsd_dir, "AM", "Release-1.4", "Template.xsd")
    with open(main_xsd, "rb") as fh:
        schema_doc = etree.fromstring(_to_v1(fh.read()), parser, base_url=main_xsd)
    schema = etree.XMLSchema(etree.ElementTree(schema_doc))
    doc = etree.fromstring(opt_bytes)
    if schema.validate(doc):
        return []
    return [str(e) for e in schema.error_log]


def load_source(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("source", help="template source JSON (templates/src/<id>.json)")
    ap.add_argument("-o", "--output", help="write the OPT here (default: stdout)")
    ap.add_argument("--check", metavar="OPT", help="exit 1 unless OPT equals the generated bytes")
    ap.add_argument("--paths", action="store_true", help="print the predicted FLAT paths as JSON")
    ap.add_argument("--xsd", action="store_true", help="also validate against the OPT 1.4 XSD (needs lxml)")
    ap.add_argument("--archetypes", default=ARCHETYPE_DIR, help="vendored archetype directory")
    args = ap.parse_args(argv)
    source = load_source(args.source)
    if args.paths:
        print(json.dumps(predict_flat_paths(source, args.archetypes), indent=2, ensure_ascii=False))
        return 0
    data = build(source, args.archetypes)
    if args.xsd:
        errors = xsd_errors(data)
        print("XSD (OPT 1.4 Template.xsd): %s" % ("valid" if not errors else "INVALID"), file=sys.stderr)
        for e in errors[:20]:
            print("  " + e, file=sys.stderr)
        if errors:
            return 1
    if args.check:
        with open(args.check, "rb") as fh:
            same = fh.read() == data
        print("%s: %s" % (args.check, "identical" if same else "DIFFERS"))
        return 0 if same else 1
    if args.output:
        with open(args.output, "wb") as fh:
            fh.write(data)
    else:
        sys.stdout.buffer.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
