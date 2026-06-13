"""Constants for the WashWise integration."""

from datetime import timedelta

DOMAIN = "washwise"
PLATFORMS = ["binary_sensor", "sensor", "button"]

# Minimum supported Home Assistant version.
MIN_HA_VERSION = "2024.10.0"

# Weather condition codes that block washing.
BAD_CONDITIONS = (
    "lightning-rainy",
    "rainy",
    "pouring",
    "snowy",
    "snowy-rainy",
    "hail",
    "exceptional",
)

# Severity (0 = mild, 1 = severe) used for score weighting.
BAD_CONDITION_SEVERITY = {
    "rainy": 0.5,
    "pouring": 1.0,
    "snowy": 0.7,
    "snowy-rainy": 0.8,
    "hail": 1.0,
    "lightning-rainy": 1.0,
    "exceptional": 1.0,
}

# Provider quirk fallback chains (used by weather_source._normalize).
PRECIP_KEYS = ("precipitation", "precipitation_amount", "precip_mm", "rain")
TMIN_KEYS = ("templow", "temp_min", "temperature_min", "temp_low")
TMAX_KEYS = ("temperature", "temp", "temperature_max", "temp_high")
TIME_KEYS = ("datetime", "time", "timestamp")

# Categories — preset thresholds + default icon.
CATEGORY_PRESETS = {
    "car": {
        "days": 3,
        "precip_threshold_mm": 0.2,
        "freeze_check": True,
        "invert": False,
        "icon": "mdi:car",
    },
    "motorcycle": {
        "days": 2,
        "precip_threshold_mm": 0.5,
        "freeze_check": True,
        "invert": False,
        "icon": "mdi:motorcycle",
    },
    "bicycle": {
        "days": 2,
        "precip_threshold_mm": 0.8,
        "freeze_check": False,
        "invert": False,
        "icon": "mdi:bicycle",
    },
    "boat": {
        "days": 5,
        "precip_threshold_mm": 0.1,
        "freeze_check": True,
        "invert": False,
        "icon": "mdi:sail-boat",
    },
    "rv_camper": {
        "days": 5,
        "precip_threshold_mm": 0.1,
        "freeze_check": True,
        "invert": False,
        "icon": "mdi:rv-truck",
    },
    "windows_house": {
        "days": 1,
        "precip_threshold_mm": 1.0,
        "freeze_check": False,
        "invert": False,
        "icon": "mdi:window-closed-variant",
    },
    "solar_panels": {
        "days": 0,
        "precip_threshold_mm": 0.0,
        "freeze_check": False,
        "invert": True,
        "icon": "mdi:solar-panel",
    },
    "patio_deck": {
        "days": 2,
        "precip_threshold_mm": 0.5,
        "freeze_check": False,
        "invert": False,
        "icon": "mdi:deck",
    },
    "garden_irrigation": {
        "days": 1,
        "precip_threshold_mm": 2.0,
        "freeze_check": False,
        "invert": True,
        "icon": "mdi:sprinkler",
    },
    "custom": {
        "days": 3,
        "precip_threshold_mm": 0.2,
        "freeze_check": True,
        "invert": False,
        "icon": "mdi:water",
    },
}

# Defaults.
DEFAULT_CATEGORY = "car"
DEFAULT_FORECAST_TYPE = "daily"
DEFAULT_FREEZE_TEMP_C = 0.0
DEFAULT_PRECIP_WEIGHT = 40
DEFAULT_FREEZE_WEIGHT = 30
DEFAULT_CONDITION_WEIGHT = 30

# Storage.
STORAGE_VERSION = 1
STORAGE_KEY_FMT = "washwise.{entry_id}"

# Provider health GC: keep stale records this long after entity removed from config.
STALE_PROVIDER_TTL_DAYS = 30

# Coordinator update interval (overridable via options).
SCAN_INTERVAL = timedelta(minutes=15)

# Config keys.
CONF_WEATHER_ENTITIES = "weather_entities"
CONF_NAME = "name"
CONF_CATEGORY = "category"
CONF_CUSTOMIZE_THRESHOLDS = "customize_thresholds"
CONF_DAYS = "days"
CONF_PRECIP_THRESHOLD = "precip_threshold_mm"
CONF_FREEZE_CHECK = "freeze_check"
CONF_FORECAST_TYPE = "forecast_type"
CONF_BAD_CONDITIONS = "bad_conditions"
CONF_PRECIP_WEIGHT = "precip_weight"
CONF_FREEZE_WEIGHT = "freeze_weight"
CONF_CONDITION_WEIGHT = "condition_weight"
CONF_EXTRA_ENTITIES = "extra_entities"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"
CONF_RAIN_GAUGE_ENTITY = "rain_gauge_entity"
CONF_RAIN_GAUGE_THRESHOLD_MM = "rain_gauge_threshold_mm"
CONF_IRRIGATION_SWITCH_ENTITY = "irrigation_switch_entity"

DEFAULT_RAIN_GAUGE_THRESHOLD_MM = 5.0
CONF_TEMPERATURE_UNIT = "temperature_unit"

# Allowed values for ``CONF_TEMPERATURE_UNIT``. ``auto`` (default) reads the
# unit from the source weather entity / HA system unit; the explicit options
# force a specific unit when the provider's metadata is wrong or missing.
TEMPERATURE_UNIT_AUTO = "auto"
TEMPERATURE_UNIT_CELSIUS = "celsius"
TEMPERATURE_UNIT_FAHRENHEIT = "fahrenheit"
TEMPERATURE_UNIT_KELVIN = "kelvin"
TEMPERATURE_UNIT_OPTIONS = (
    TEMPERATURE_UNIT_AUTO,
    TEMPERATURE_UNIT_CELSIUS,
    TEMPERATURE_UNIT_FAHRENHEIT,
    TEMPERATURE_UNIT_KELVIN,
)
DEFAULT_TEMPERATURE_UNIT = TEMPERATURE_UNIT_AUTO
