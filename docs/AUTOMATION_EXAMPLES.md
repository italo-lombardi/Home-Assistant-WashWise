# WashWise automation examples

Ready-to-use automations built on WashWise's [named triggers](../README.md#automation-triggers).
Copy one into **Settings → Automations → Create → Edit in YAML**, or adapt the trigger/action to your setup.

All triggers accept an optional `config_entry_id` to scope them to a single WashWise
entry; omit it to fire for any entry. The payload of every trigger carries `entry_id`,
`title`, `category`, `can_wash`, and `reason` (plus `should_wash`, `score`,
`days_until_wash`, and the `previous_*` fields on `verdict_changed`).

---

## 1. Notify when it becomes a good time to wash the car

Uses `should_wash_on` — fires only when the weather is good **and** enough days have
passed since the last wash, so you aren't nagged the day after you washed.

```yaml
alias: "Car: time to wash"
triggers:
  - trigger: washwise.should_wash_on
    config_entry_id: REPLACE_WITH_ENTRY_ID   # the Car entry; omit to match any
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: "🚗 Good day to wash the car"
      message: >
        {{ trigger.event.data.title }} — {{ trigger.event.data.reason }},
        score {{ trigger.event.data.score }}/100.
mode: single
```

## 2. Remind me each morning only while a wash is still recommended

Pairs the trigger with a time condition: `should_wash_on` arms it, and a 9 AM
template keeps reminding until you mark the car washed (which flips `should_wash` off).

```yaml
alias: "Car: morning wash reminder"
triggers:
  - trigger: time
    at: "09:00:00"
conditions:
  - condition: state
    entity_id: binary_sensor.washwise_car_should_wash
    state: "on"
actions:
  - action: notify.mobile_app_my_phone
    data:
      message: "Still a good day to wash the car ☀️"
mode: single
```

## 3. Turn on a reminder light when the weather stops allowing a wash

Uses `can_wash_off` (weather-only) — independent of wash timing. Good for a visible
cue that a planned wash window just closed.

```yaml
alias: "Wash window closed → amber lamp"
triggers:
  - trigger: washwise.can_wash_off
actions:
  - action: light.turn_on
    target:
      entity_id: light.hallway
    data:
      color_name: orange
      brightness_pct: 40
mode: single
```

## 4. Log every verdict change to the logbook

Uses `verdict_changed` (the superset trigger) to record any change — `can_wash`,
`should_wash`, score, reason, or days-until-wash — with before/after booleans.

```yaml
alias: "WashWise: log verdict changes"
triggers:
  - trigger: washwise.verdict_changed
actions:
  - action: logbook.log
    data:
      name: WashWise
      message: >
        {{ trigger.event.data.title }}:
        can_wash {{ trigger.event.data.previous_can_wash }} →
        {{ trigger.event.data.can_wash }},
        should_wash {{ trigger.event.data.previous_should_wash }} →
        {{ trigger.event.data.should_wash }}
        ({{ trigger.event.data.reason }}, score {{ trigger.event.data.score }})
      entity_id: "{{ 'binary_sensor.washwise_' ~ trigger.event.data.title | slugify ~ '_can_wash' }}"
mode: queued
```

## 5. Garden irrigation — skip watering when rain is forecast

For a `garden_irrigation` entry, `can_wash_on` means rain is expected (inverted logic),
so watering should be skipped. Use it to turn an irrigation switch off automatically.

```yaml
alias: "Garden: skip watering when rain forecast"
triggers:
  - trigger: washwise.can_wash_on
    config_entry_id: REPLACE_WITH_GARDEN_ENTRY_ID
actions:
  - action: switch.turn_off
    target:
      entity_id: switch.garden_irrigation
mode: single
```

## 6. Announce on a speaker when a wash is due, only during the day

Combines `should_wash_on` with a time condition so you don't get a TTS announcement
overnight.

```yaml
alias: "Car: announce wash is due"
triggers:
  - trigger: washwise.should_wash_on
conditions:
  - condition: time
    after: "08:00:00"
    before: "20:00:00"
actions:
  - action: tts.google_translate_say
    target:
      entity_id: media_player.kitchen
    data:
      message: "It's a good day to wash the {{ trigger.event.data.title }}."
mode: single
```

---

### Finding an entry's `config_entry_id`

Settings → Devices & Services → WashWise → the entry's ⋮ menu → the id is in the URL,
or use Developer Tools → Actions and pick the entry in any WashWise service's target.
Leave `config_entry_id` out of a trigger to match **all** WashWise entries.
