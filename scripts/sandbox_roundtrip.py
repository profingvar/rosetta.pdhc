#!/usr/bin/env python3
"""openEHR round-trip harness against the ASHA-PDHC sandbox cdr1 (#507).

Create EHR -> POST a FLAT composition -> AQL read-back -> assert
magnitudes+units survived. This is the conformance proof for the openEHR
projection: it exercises the server's path indexing (via AQL), not just
"did it store a blob".

NOTE (#512): the sandbox is UI-wrapped (no /rest/openehr/v1), so this drives
the ASHA Razor handlers (form login + antiforgery + multipart). If/when a
spec-REST target is available, port the three POSTs to /rest/openehr/v1.

Env: OEHR_USER, OEHR_PASS (demo creds, operator-held); OEHR_BASE optional.
Read-mostly on the target, but it DOES create an EHR + one composition on
cdr1 each run (test data on the demo instance).
"""
import os, re, json, urllib.request, urllib.parse, http.cookiejar, datetime, sys

B=os.environ.get("OEHR_BASE","https://openehr.phanera.se").rstrip("/")
U=os.environ["OEHR_USER"]; P=os.environ["OEHR_PASS"]
HERE=os.path.dirname(os.path.abspath(__file__))
FLAT=os.path.join(HERE,"..","templates","samples","pdhc_vitals_roundtrip.flat.json")
TEMPLATE_ID="pdhc_vitals.v1"; INSTANCE="cdr1"
EXPECT={"temp 37.2":"37.2","systolic 128":"128","diastolic 82":"82","unit Cel":"Cel","unit mm[Hg]":"mm[Hg]"}

cj=http.cookiejar.CookieJar()
op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
op.addheaders=[("User-Agent","rosetta-roundtrip/1.0")]
tok=lambda h: (re.search(r'__RequestVerificationToken"[^>]*value="([^"]+)"',h) or [None,""])[1]
def get(u): return op.open(u,timeout=60).read().decode("utf-8","replace")
def post(u,fields,files=None):
    if files:
        bnd="----rt"; parts=[f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k,v in fields.items()]
        for k,(fn,ct,data) in files.items():
            parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: {ct}\r\n\r\n{data}\r\n')
        body=("".join(parts)+f"--{bnd}--\r\n").encode(); ct=f"multipart/form-data; boundary={bnd}"
    else:
        body=urllib.parse.urlencode(fields).encode(); ct="application/x-www-form-urlencoded"
    return op.open(urllib.request.Request(u,body,{"Content-Type":ct}),timeout=90).read().decode("utf-8","replace")

# 1 login
post(f"{B}/Login?ReturnUrl=%2FDashboard",{"Input.Username":U,"Input.Password":P,
     "__RequestVerificationToken":tok(get(f"{B}/Login?ReturnUrl=%2FDashboard")),"ReturnUrl":"/Dashboard"})
# 2 create EHR
pid="pdhc-rt-"+datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%S")
r=post(f"{B}/Tools/Ehrs?handler=CreateEhrs",{"instanceName":INSTANCE,"patientIds":pid,
     "__RequestVerificationToken":tok(get(f"{B}/Tools/Ehrs?instanceName={INSTANCE}&mode=browse"))})
ehr=(re.findall(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',r) or [None])[0]
if not ehr: sys.exit("could not create/capture EHR")
# 3 POST composition
r=post(f"{B}/Tools/Compositions?handler=UploadComposition",
   {"instanceName":INSTANCE,"ehrId":ehr,"templateId":TEMPLATE_ID,"flatJson":open(FLAT).read(),
    "__RequestVerificationToken":tok(get(f"{B}/Tools/Compositions?instanceName={INSTANCE}"))})
print(f"EHR {ehr} <- composition: {'uploaded' if 'uppladd' in r.lower() or 'success' in r.lower() else 'uncertain'}")
# 4 AQL read-back
aql=("SELECT t/data[at0002]/events[at0003]/data[at0001]/items[at0004]/value/magnitude AS temp_c, "
 "t/data[at0002]/events[at0003]/data[at0001]/items[at0004]/value/units AS temp_unit, "
 "b/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/magnitude AS systolic, "
 "b/data[at0001]/events[at0006]/data[at0003]/items[at0005]/value/magnitude AS diastolic, "
 "b/data[at0001]/events[at0006]/data[at0003]/items[at0004]/value/units AS bp_unit "
 "FROM EHR e CONTAINS COMPOSITION c CONTAINS (OBSERVATION t[openEHR-EHR-OBSERVATION.body_temperature.v2] "
 "AND OBSERVATION b[openEHR-EHR-OBSERVATION.blood_pressure.v2])")
import html as H
res=H.unescape(get(f"{B}/Tools/Aql?handler=Aql&instanceName={INSTANCE}&query="+urllib.parse.quote(aql)))
fails=[k for k,v in EXPECT.items() if v not in res]
for k,v in EXPECT.items(): print(f"  [{'PASS' if v in res else 'FAIL'}] {k}")
print("RESULT:", "PASS" if not fails else f"FAIL ({', '.join(fails)})")
sys.exit(0 if not fails else 1)
