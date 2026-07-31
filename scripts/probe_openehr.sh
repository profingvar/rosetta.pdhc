#!/usr/bin/env bash
# probe_openehr.sh — read-only capability probe of an external openEHR CDR (#508).
#
# Answers, without writing anything:
#   1. Does it speak the openEHR REST API, and at what base/version?
#   2. Which vendor flavour (EHRbase spec API vs Better/EHRScape)?
#   3. What auth does it require (we detect 401 vs 200)?
#   4. Does it already hold any operational templates?
#
# It performs ONLY GET requests. Uploading our own OPT (the real
# "can we upload?" test) is a POST and is deliberately NOT done here — that is
# step 2, once we have a template (#502) and an explicit go-ahead.
#
# Usage:
#   OEHR_BASE=https://sandbox.example.org [auth vars] ./probe_openehr.sh
#
# Auth (pick one; omit all for an open server):
#   OEHR_BASIC_USER + OEHR_BASIC_PASS       -> HTTP Basic
#   OEHR_BEARER                             -> Authorization: Bearer <token>
#   OEHR_HEADER  (e.g. "X-Api-Key: abc123") -> arbitrary header, repeatable via OEHR_HEADER2
#
# Nothing is echoed that would leak a secret: only HTTP status, Content-Type,
# and a short non-sensitive body snippet are printed.
set -uo pipefail

BASE="${OEHR_BASE:-}"
[ -z "$BASE" ] && { echo "ERROR: set OEHR_BASE=https://<sandbox-host>"; exit 2; }
BASE="${BASE%/}"   # strip trailing slash

# ---- assemble auth args (never printed) ----
AUTH=()
if [ -n "${OEHR_BASIC_USER:-}" ]; then
  AUTH+=(-u "${OEHR_BASIC_USER}:${OEHR_BASIC_PASS:-}")
fi
[ -n "${OEHR_BEARER:-}" ] && AUTH+=(-H "Authorization: Bearer ${OEHR_BEARER}")
[ -n "${OEHR_HEADER:-}"  ] && AUTH+=(-H "${OEHR_HEADER}")
[ -n "${OEHR_HEADER2:-}" ] && AUTH+=(-H "${OEHR_HEADER2}")

CURL=(curl -sS --max-time 20 -o /tmp/oehr_body.$$ -w '%{http_code}|%{content_type}')

probe() {   # probe <label> <method> <path> [extra curl args...]
  local label="$1" method="$2" path="$3"; shift 3
  local url="${BASE}${path}"
  local out code ctype
  out=$("${CURL[@]}" -X "$method" "${AUTH[@]}" "$@" "$url" 2>/tmp/oehr_err.$$) || out="000|curl-error"
  code="${out%%|*}"; ctype="${out#*|}"
  printf '  %-34s %s  ->  %s  [%s]\n' "$label" "$method" "$code" "${ctype:-?}"
  # print a short, non-secret snippet for context
  if [ -s /tmp/oehr_body.$$ ]; then
    head -c 240 /tmp/oehr_body.$$ | tr '\n' ' ' | sed 's/  */ /g'
    echo
  fi
  [ -s /tmp/oehr_err.$$ ] && sed 's/^/    curl: /' /tmp/oehr_err.$$
}

echo "=== openEHR capability probe: $BASE ==="
echo "auth: $( ((${#AUTH[@]})) && echo 'configured' || echo 'NONE (open server assumed)')"
echo

echo "[A] EHRbase / openEHR-spec REST (base /rest/openehr/v1)"
probe "status (EHRbase)"          GET "/rest/status"
probe "conformance"               GET "/rest/openehr/v1"                 -H 'Accept: application/json'
probe "template list adl1.4"      GET "/rest/openehr/v1/definition/template/adl1.4" -H 'Accept: application/json'
probe "template list adl2"        GET "/rest/openehr/v1/definition/template/adl2"   -H 'Accept: application/json'
echo

echo "[B] Better / EHRScape REST (base /rest/v1)"
probe "EHRScape template list"    GET "/rest/v1/template"                -H 'Accept: application/json'
probe "EHRScape ehr"              GET "/rest/v1/ehr"                     -H 'Accept: application/json'
echo

echo "[C] Generic reachability"
probe "root"                      GET "/"
probe "openapi/swagger"           GET "/swagger-ui.html"
echo

rm -f /tmp/oehr_body.$$ /tmp/oehr_err.$$
cat <<'NOTE'
Reading the result:
  200 on a template-list  -> we can talk to it; that list answers "does it hold
                             any templates yet?" (empty list = greenfield, good).
  401/403                 -> reachable but auth is wrong/missing; fix creds.
  200 under [A] but 404 under [B] (or vice-versa) -> tells us the vendor flavour,
                             which fixes every endpoint path downstream.
  000/curl-error          -> not reachable from here (network/DNS/TLS) — decide
                             whether to run from miserver or via a tunnel.
Next (NOT done by this script): upload our own .opt (a POST) to answer the real
"can we host our own template?" gate — that needs #502 (a template) first.
NOTE
