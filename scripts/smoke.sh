#!/usr/bin/env bash
# WashWise live smoke test — runs REST probes against a running HA instance.
#
# Assumes the integration is already deployed to the HA at $HA (default the
# local devcontainer on :8123) and a bearer token is in $HA_TOKEN (or minted
# into /tmp/ww_token.txt by the deploy runbook). Non-destructive: it creates a
# few temporary config entries prefixed "Smoke " and deletes them at the end.
#
# Usage:
#   HA_TOKEN=$(cat /tmp/ww_token.txt) scripts/smoke.sh
#   HA=http://localhost:8123 HA_TOKEN=... scripts/smoke.sh
#
# Exit code is the number of failed checks (0 = all green).

set -uo pipefail

HA="${HA:-http://localhost:8123}"
HA_TOKEN="${HA_TOKEN:-$(cat /tmp/ww_token.txt 2>/dev/null)}"
WEATHER="${WEATHER:-weather.forecast_home}"

if [ -z "$HA_TOKEN" ]; then
  echo "FATAL: no HA_TOKEN (set it, or mint into /tmp/ww_token.txt)" >&2
  exit 99
fi

AUTH=(-H "Authorization: Bearer $HA_TOKEN")
JSON=(-H "Content-Type: application/json")
FAILS=0
CREATED_ENTRY_IDS=()

pass() { echo "  ✓ $1"; }
fail() { echo "  ✗ $1"; FAILS=$((FAILS + 1)); }

api() { curl -s "${AUTH[@]}" "$@"; }

echo "== WashWise smoke @ $HA =="

# --- 0. API reachable ---------------------------------------------------------
code=$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "$HA/api/")
[ "$code" = "200" ] && pass "API reachable (200)" || { fail "API returned $code"; exit "$FAILS"; }

# --- 1. All WashWise entries loaded (poll — mock providers retry on cold start) -
for attempt in 1 2 3 4 5 6; do
  OUT=$(api "$HA/api/config/config_entries/entry" | python3 -c "
import json, sys
ww = [e for e in json.load(sys.stdin) if e.get('domain') == 'washwise']
bad = [e for e in ww if e['state'] != 'loaded']
print(f'{len(ww) - len(bad)}/{len(ww)}')
print(','.join(f'{e[\"state\"]}:{e[\"title\"]}' for e in bad))
")
  RATIO=$(echo "$OUT" | sed -n 1p)
  BAD=$(echo "$OUT" | sed -n 2p)
  [ -z "$BAD" ] && break
  sleep 5
done
if [ -z "$BAD" ]; then
  pass "entries loaded: $RATIO"
else
  fail "entries not all loaded: $RATIO"
  echo "    $BAD" | tr ',' '\n' | sed 's/^/    /'
fi

# --- 2. Create a fresh non-customize car entry (wind-seed + feature surface) --
FLOW=$(api -X POST "${JSON[@]}" -d '{"handler":"washwise"}' "$HA/api/config/config_entries/flow")
FID=$(echo "$FLOW" | python3 -c "import json,sys; print(json.load(sys.stdin)['flow_id'])")
RES=$(api -X POST "${JSON[@]}" \
  -d "{\"weather_entities\":[\"$WEATHER\"],\"name\":\"Smoke Car\",\"category\":\"car\",\"customize_thresholds\":false}" \
  "$HA/api/config/config_entries/flow/$FID")
ENTRY_ID=$(echo "$RES" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('result') or {}).get('entry_id','') if isinstance(d.get('result'),dict) else '')")
[ -n "$ENTRY_ID" ] && CREATED_ENTRY_IDS+=("$ENTRY_ID")
echo "$RES" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  '+('✓' if d.get('type')=='create_entry' else '✗')+' created Smoke Car:', d.get('type'))"
[ "$(echo "$RES" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("type"))')" = "create_entry" ] || FAILS=$((FAILS + 1))
sleep 8

CAN_WASH="binary_sensor.washwise_smoke_car_can_wash"

# --- 3. Customize-create pre-fills the wind cutoff with the category default --
# Drive a CUSTOMIZE create to the thresholds step and read the wind field's
# suggested_value from the returned data_schema. Guards the bug where the
# customize path left the wind box empty (seed only ran the non-customize path).
# car preset default = 35; an invert category (solar_panels) omits wind →
# suggested absent/null. Both flows are aborted (no entry created).
check_wind_prefill() {
  local category="$1" expect="$2"
  local flow fid step
  flow=$(api -X POST "${JSON[@]}" -d '{"handler":"washwise"}' "$HA/api/config/config_entries/flow")
  fid=$(echo "$flow" | python3 -c "import json,sys; print(json.load(sys.stdin)['flow_id'])")
  step=$(api -X POST "${JSON[@]}" \
    -d "{\"weather_entities\":[\"$WEATHER\"],\"name\":\"Smoke Cust $category\",\"category\":\"$category\",\"customize_thresholds\":true}" \
    "$HA/api/config/config_entries/flow/$fid")
  api -X DELETE "$HA/api/config/config_entries/flow/$fid" >/dev/null 2>&1  # abort, no entry
  echo "$step" | EXPECT="$expect" CAT="$category" python3 -c "
import json, os, sys
d = json.load(sys.stdin)
if d.get('step_id') != 'thresholds':
    print(f'  ✗ wind-prefill[{os.environ[\"CAT\"]}]: did not reach thresholds step'); sys.exit(1)
wind = next((f for f in d.get('data_schema', []) if f.get('name') == 'wind_threshold_kmh'), None)
sug = (wind or {}).get('description', {}).get('suggested_value')
exp = os.environ['EXPECT']
if exp == 'none':
    ok = sug is None
    print(f'  {\"✓\" if ok else \"✗\"} wind-prefill[{os.environ[\"CAT\"]}]: suggested={sug!r} (expected none)')
    sys.exit(0 if ok else 1)
ok = sug == float(exp) or sug == int(exp)
print(f'  {\"✓\" if ok else \"✗\"} wind-prefill[{os.environ[\"CAT\"]}]: suggested={sug!r} (expected {exp})')
sys.exit(0 if ok else 1)
" || FAILS=$((FAILS + 1))
}
check_wind_prefill "car" "35"
check_wind_prefill "solar_panels" "none"

# --- 4. Enriched diagnostics attributes present on can_wash ------------------
api "$HA/api/states/$CAN_WASH" | python3 -c "
import json, sys
try:
    a = json.load(sys.stdin).get('attributes', {})
except Exception:
    print('  ✗ diagnostics: can_wash state not readable'); sys.exit(1)
# Always-present keys regardless of weather. worst_condition / precip_total_mm /
# temps / max_wind_kmh are forecast-dependent (absent on a clear mock forecast),
# so they are NOT required here — absence is correct, not a failure.
required = ['score', 'reason', 'days_analyzed', 'active_weather_entity', 'freeze_risk']
missing = [k for k in required if k not in a]
if missing:
    print(f'  ✗ diagnostics: missing {missing}'); sys.exit(1)
# At least confirm the aggregate machinery ran: freeze_risk is bool, and if the
# forecast carried any precip it surfaced as a number.
opt = {k: a[k] for k in ('precip_total_mm','min_temp','max_temp','worst_condition','max_wind_kmh') if k in a}
print(f'  ✓ diagnostics attrs present (score={a[\"score\"]}, reason={a[\"reason\"]}, '
      f'freeze_risk={a[\"freeze_risk\"]}; forecast-derived: {opt or \"none (clear mock)\"})')
" || FAILS=$((FAILS + 1))

# --- 5. Service targeting by entity_id (the card path) + latency -------------
# mark_washed by entity_id → last_washed flips from unknown to a timestamp.
# Also assert the state reflects the change FAST: the synchronous-push fix means
# the service must not block on a weather forecast fetch. Poll the sensor right
# after the (blocking) service call and require the change within ~2s. A
# regression to async_request_refresh would reintroduce the multi-second fetch.
LAST_WASHED="sensor.washwise_smoke_car_last_washed"
before=$(api "$HA/api/states/$LAST_WASHED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('state','?'))")
start=$(python3 -c "import time; print(time.time())")
api -X POST "${JSON[@]}" -d "{\"entity_id\":\"$CAN_WASH\"}" "$HA/api/services/washwise/mark_washed" >/dev/null
after="$before"
for _ in $(seq 1 20); do   # up to ~2s (20 × 0.1s)
  after=$(api "$HA/api/states/$LAST_WASHED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('state','?'))")
  { [ "$after" != "$before" ] && [ "$after" != "unknown" ] && [ "$after" != "unavailable" ]; } && break
  sleep 0.1
done
elapsed=$(python3 -c "import time; print(round(time.time() - $start, 2))")
if [ "$after" != "unknown" ] && [ "$after" != "unavailable" ] && [ "$after" != "$before" ]; then
  pass "mark_washed via entity_id → last_washed set in ${elapsed}s ($before → $after)"
  # Soft latency guard: warn (don't fail CI-less smoke hard) if it took long.
  python3 -c "import sys; sys.exit(0 if $elapsed <= 2.0 else 1)" \
    || echo "  ⚠ latency ${elapsed}s > 2s — the sync-push fast path may have regressed"
else
  fail "mark_washed via entity_id → last_washed unchanged after ${elapsed}s ($before → $after)"
fi

# --- 6. Snooze + clear_snooze by entity_id -----------------------------------
# Verify via the snooze_remaining sensor's snooze_until ATTRIBUTE + service HTTP
# status, NOT the can_wash reason (the mock provider recalcs the verdict between
# calls, racing `reason`). The active snooze shows as a future snooze_until.
SNOOZE_REMAIN="sensor.washwise_smoke_car_snooze_remaining"
code=$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "${JSON[@]}" \
  -d "{\"entity_id\":\"$CAN_WASH\"}" "$HA/api/services/washwise/snooze")
until_val=""
for _ in 1 2 3 4 5; do
  sleep 2
  until_val=$(api "$HA/api/states/$SNOOZE_REMAIN" | python3 -c "import json,sys; print(json.load(sys.stdin).get('attributes',{}).get('snooze_until') or '')")
  [ -n "$until_val" ] && break
done
if [ "$code" = "200" ] && [ -n "$until_val" ]; then
  pass "snooze via entity_id → 200, snooze_until=$until_val"
else
  fail "snooze via entity_id → http=$code snooze_until='$until_val'"
fi
code=$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "${JSON[@]}" \
  -d "{\"entity_id\":\"$CAN_WASH\"}" "$HA/api/services/washwise/clear_snooze")
until_val="x"
for _ in 1 2 3 4 5; do
  sleep 2
  until_val=$(api "$HA/api/states/$SNOOZE_REMAIN" | python3 -c "import json,sys; print(json.load(sys.stdin).get('attributes',{}).get('snooze_until') or '')")
  [ -z "$until_val" ] && break
done
if [ "$code" = "200" ] && [ -z "$until_val" ]; then
  pass "clear_snooze via entity_id → 200, snooze_until cleared"
else
  fail "clear_snooze via entity_id → http=$code snooze_until='$until_val'"
fi

# --- 7. No WashWise errors in the log ----------------------------------------
# Match on the log LEVEL token (" ERROR "/" CRITICAL "), not the substring
# "error"/"exception": a mock provider is named weather.washwise_exceptionAL, so
# a substring match would false-positive on every benign DEBUG line mentioning
# it. Exclude the intentional HomeAssistantError the negative tests below raise
# (expected, logged by the api component, not a product fault).
errs=$(docker exec serene_booth grep -i washwise /tmp/ha-boot.log 2>/dev/null \
  | sed 's/\x1b\[[0-9;]*m//g' \
  | grep -E " (ERROR|CRITICAL) " \
  | grep -v entity_availability \
  | grep -v "is not a registered WashWise entity")
if [ -z "$errs" ]; then
  pass "no washwise errors in boot log"
else
  fail "washwise errors in boot log"
  echo "$errs" | tail -5 | sed 's/^/    /'
fi

# --- 8. Service negatives (these intentionally log errors — run last) --------
code=$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "${JSON[@]}" \
  -d '{"entity_id":"binary_sensor.not_a_washwise_entity"}' "$HA/api/services/washwise/snooze")
[ "$code" = "500" ] && pass "unregistered entity_id → 500 (error, not silent)" || fail "unregistered entity_id → $code (want 500)"
code=$(curl -s -o /dev/null -w '%{http_code}' "${AUTH[@]}" "${JSON[@]}" \
  -d "{\"entity_id\":\"$CAN_WASH\",\"entry_id\":\"x\"}" "$HA/api/services/washwise/snooze")
[ "$code" = "400" ] && pass "entry_id+entity_id together → 400 (vol.Exclusive)" || fail "both targets → $code (want 400)"

# --- cleanup ------------------------------------------------------------------
for eid in "${CREATED_ENTRY_IDS[@]}"; do
  curl -s -o /dev/null "${AUTH[@]}" -X DELETE "$HA/api/config/config_entries/entry/$eid"
done
[ ${#CREATED_ENTRY_IDS[@]} -gt 0 ] && echo "  (cleaned up ${#CREATED_ENTRY_IDS[@]} smoke entr$([ ${#CREATED_ENTRY_IDS[@]} -eq 1 ] && echo y || echo ies))"

echo "== smoke: $FAILS failed =="
exit "$FAILS"
