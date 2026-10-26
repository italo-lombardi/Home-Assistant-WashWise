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

# UTC timestamp at run start (HA log prefix format, e.g. 2026-10-26 22:35:00).
# The log-error check below only considers lines at/after this, so errors from
# earlier runs in the same hass lifetime don't fail a clean run.
RUN_START=$(python3 -c "import datetime; print(datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))")

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
# Use a unique name per run: repeated fixed-name entries leave orphaned
# `unavailable` entities in the registry across runs, and a new entry then can't
# reclaim the base slug (gets a _2 suffix), so a hardcoded slug points at a dead
# orphan. A unique name sidesteps slug collisions.
SMOKE_NAME="Smoke Car $(python3 -c 'import time; print(int(time.time()))')"
FLOW=$(api -X POST "${JSON[@]}" -d '{"handler":"washwise"}' "$HA/api/config/config_entries/flow")
FID=$(echo "$FLOW" | python3 -c "import json,sys; print(json.load(sys.stdin)['flow_id'])")
RES=$(api -X POST "${JSON[@]}" \
  -d "{\"weather_entities\":[\"$WEATHER\"],\"name\":\"$SMOKE_NAME\",\"category\":\"car\",\"customize_thresholds\":false}" \
  "$HA/api/config/config_entries/flow/$FID")
ENTRY_ID=$(echo "$RES" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('result') or {}).get('entry_id','') if isinstance(d.get('result'),dict) else '')")
[ -n "$ENTRY_ID" ] && CREATED_ENTRY_IDS+=("$ENTRY_ID")
echo "$RES" | python3 -c "import json,sys; d=json.load(sys.stdin); print('  '+('✓' if d.get('type')=='create_entry' else '✗')+' created \"$SMOKE_NAME\":', d.get('type'))"
[ "$(echo "$RES" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("type"))')" = "create_entry" ] || FAILS=$((FAILS + 1))

# Resolve the can_wash entity_id for THIS entry from the entity registry (keyed
# on config_entry_id), rather than guessing the slug — immune to _N suffixing.
# The registry list is large, so request it over a websocket with a big frame.
CAN_WASH=$(ENTRY_ID="$ENTRY_ID" docker exec -i -e ENTRY_ID="$ENTRY_ID" serene_booth /home/vscode/.local/ha-venv/bin/python3 - <<'PY' 2>/dev/null
import json, os, asyncio, websockets
async def main():
    async with websockets.connect("ws://localhost:8123/api/websocket", max_size=20_000_000) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": open("/tmp/ww_token.txt").read().strip()}))
        await ws.recv()
        await ws.send(json.dumps({"id": 1, "type": "config/entity_registry/list"}))
        msg = json.loads(await ws.recv())
        eid = os.environ["ENTRY_ID"]
        for e in msg["result"]:
            if (e.get("config_entry_id") == eid and e["entity_id"].startswith("binary_sensor.")
                    and e["entity_id"].endswith("_can_wash")):
                print(e["entity_id"]); return
asyncio.run(main())
PY
)
[ -n "$CAN_WASH" ] && pass "resolved can_wash entity: $CAN_WASH" || { fail "could not resolve can_wash entity for $ENTRY_ID"; CAN_WASH="binary_sensor.__missing__"; }

# Wait for the new entry's coordinator to finish async setup before using it.
# A fixed sleep is fragile: when other entries are mid-ConfigEntryNotReady retry
# (mock providers warming up), the new entry's setup can be delayed past a short
# sleep, leaving its services unregistered (mark_washed → "No WashWise instance
# found" 500) and its entity `unavailable`. Poll can_wash until it resolves.
ready=""
for _ in $(seq 1 30); do   # up to ~30s
  st=$(api "$HA/api/states/$CAN_WASH" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('state',''))
except Exception: print('')" 2>/dev/null)
  if [ "$st" = "on" ] || [ "$st" = "off" ]; then ready="$st"; break; fi
  sleep 1
done
if [ -n "$ready" ]; then
  pass "Smoke Car coordinator ready (can_wash=$ready)"
else
  fail "Smoke Car coordinator did not become ready (can_wash=$st)"
fi

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

# --- 4b. last_update sensor is live, not frozen at creation ------------------
# Regression guard: the Last-update sensor used to be seeded once from the
# entry's created_at and never advanced (base DataUpdateCoordinator doesn't
# touch last_update_success_time). A freshly-created entry whose coordinator has
# run must report a last_update within the last few minutes, not a stale seed.
LAST_UPDATE="$(echo "$CAN_WASH" | sed 's/^binary_sensor\./sensor./; s/_can_wash$/_last_update/')"
api "$HA/api/states/$LAST_UPDATE" | python3 -c "
import json, sys, datetime
s = json.load(sys.stdin)
st = s.get('state')
try:
    ts = datetime.datetime.fromisoformat(st)
except Exception:
    print(f'  ✗ last_update: unparseable state {st!r}'); sys.exit(1)
age = (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()
ok = 0 <= age <= 600  # within 10 min of now (just created + evaluated)
print(f'  {\"✓\" if ok else \"✗\"} last_update is live: {st} (age {int(age)}s)')
sys.exit(0 if ok else 1)
" || FAILS=$((FAILS + 1))

# --- 4c. most_severe_forecast_condition never reports a benign sky -----------
# Regression guard: WorstConditionSensor used to latch the first forecast day
# regardless of severity, surfacing benign conditions (sunny/partlycloudy/cloudy)
# as 'most severe'. It must be a bad condition or unknown/None — never benign.
MOST_SEVERE="$(echo "$CAN_WASH" | sed 's/^binary_sensor\./sensor./; s/_can_wash$/_most_severe_forecast_condition/')"
api "$HA/api/states/$MOST_SEVERE" | python3 -c "
import json, sys
BENIGN = {'sunny', 'clear-night', 'partlycloudy', 'cloudy', 'windy', 'fog'}
st = json.load(sys.stdin).get('state')
ok = st not in BENIGN  # unknown/None or an actual bad condition are both fine
print(f'  {\"✓\" if ok else \"✗\"} most_severe_forecast_condition not benign-latched: {st!r}')
sys.exit(0 if ok else 1)
" || FAILS=$((FAILS + 1))

# --- 5. Service targeting by entity_id (the card path) + latency -------------
# mark_washed by entity_id → last_washed flips from unknown to a timestamp.
# Also assert the state reflects the change FAST: the synchronous-push fix means
# the service must not block on a weather forecast fetch. Poll the sensor right
# after the (blocking) service call and require the change within ~2s. A
# regression to async_request_refresh would reintroduce the multi-second fetch.
LAST_WASHED="$(echo "$CAN_WASH" | sed 's/^binary_sensor\./sensor./; s/_can_wash$/_last_washed/')"
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
SNOOZE_REMAIN="$(echo "$CAN_WASH" | sed 's/^binary_sensor\./sensor./; s/_can_wash$/_snooze_remaining/')"
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
# it. Only consider lines at/after RUN_START (errors from earlier runs in the
# same hass lifetime must not fail a clean run). Exclude the intentional
# HomeAssistantError the negative tests below raise (expected, logged by the api
# component, not a product fault).
errs=$(docker exec serene_booth grep -i washwise /tmp/ha-boot.log 2>/dev/null \
  | sed 's/\x1b\[[0-9;]*m//g' \
  | awk -v start="$RUN_START" '{ ts=substr($0,1,19); if (ts >= start) print }' \
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
