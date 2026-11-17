"""#728: ASHA sandbox template tools (cdr1). Creds from OEHR_USER/OEHR_PASS (.env.local).
usage: asha_templates.py explore <template_id> | upload <file.opt> | verify <template_id> <flat.json>
verify posts to a TEMPORARY EHR that ASHA deletes afterwards; the CDR is left unchanged."""
import os, re, sys, html, uuid, urllib.request
sys.path.insert(0, os.getcwd())
from app.services.openehr_transports import AshaTransport
t = AshaTransport(user=os.environ["OEHR_USER"], password=os.environ["OEHR_PASS"]); t.login(); op = t._opener()
INST = "cdr1"
def explore(tid):
    b = html.unescape(op.open(f"{t.base}/Tools/Templates?handler=Explore&instanceName={INST}&templateId={tid}", timeout=90).read().decode("utf-8", "replace"))
    return sorted(set(re.findall(r'([a-z_]+(?:/[A-Za-z0-9_:]+)+\|[a-z_]+|[a-z_]+(?:/[A-Za-z0-9_:]+)+)(?=\s|<)', b)))
def upload(path):
    page = op.open(f"{t.base}/Tools/Templates?instanceName={INST}&tab=hantera", timeout=60).read().decode()
    tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page).group(1)
    bnd = uuid.uuid4().hex; data = open(path, "rb").read()
    parts = [f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in
             (("__RequestVerificationToken", tok), ("instanceName", INST), ("tab", "hantera"))]
    parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="templateFile"; filename="{os.path.basename(path)}"\r\nContent-Type: application/xml\r\n\r\n'.encode() + data + b"\r\n")
    body = b"".join(parts) + f"--{bnd}--\r\n".encode()
    req = urllib.request.Request(f"{t.base}/Tools/Templates?handler=Upload", body, {"Content-Type": f"multipart/form-data; boundary={bnd}"})
    try: r = op.open(req, timeout=120); code, out = r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e: code, out = e.code, e.read().decode("utf-8", "replace")
    msg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(out)))
    i = max(msg.find("uppladd"), msg.find("Fel"), msg.find("rror"))
    print("upload HTTP", code, "|", msg[max(0, i-150):i+300] if i >= 0 else msg[:300])
if sys.argv[1] == "explore":
    p = explore(sys.argv[2]); print(len(p), "paths"); print("\n".join(p))
elif sys.argv[1] == "upload":
    upload(sys.argv[2])

def verify(tid, path):
    page = op.open(f"{t.base}/Tools/Templates?instanceName={INST}&tab=validera", timeout=60).read().decode()
    tok = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', page).group(1)
    bnd = uuid.uuid4().hex; data = open(path, "rb").read()
    parts = [f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in
             (("__RequestVerificationToken", tok), ("instanceName", INST), ("tab", "validera"), ("templateId", tid), ("verifyMethod", "file"))]
    parts.append(f'--{bnd}\r\nContent-Disposition: form-data; name="compositionFile"; filename="c.json"\r\nContent-Type: application/json\r\n\r\n'.encode() + data + b"\r\n")
    body = b"".join(parts) + f"--{bnd}--\r\n".encode()
    req = urllib.request.Request(f"{t.base}/Tools/Templates?tab=validera&handler=Verify", body, {"Content-Type": f"multipart/form-data; boundary={bnd}"})
    try: r = op.open(req, timeout=120); code, out = r.getcode(), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e: code, out = e.code, e.read().decode("utf-8", "replace")
    msg = re.sub(r"\s+", " ", re.sub(r"<script.*?</script>|<style.*?</style>", " ", html.unescape(out), flags=re.S))
    msg = re.sub(r"<[^>]+>", " ", msg); msg = re.sub(r"\s+", " ", msg)
    i = msg.find("Validera komposition"); print("verify HTTP", code, "|", msg[i:i+900] if i >= 0 else msg[-900:])
if sys.argv[1] == "verify": verify(sys.argv[2], sys.argv[3])
