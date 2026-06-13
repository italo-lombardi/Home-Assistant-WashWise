"""Data models for WashWise."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class ForecastDay:
    date: date
    condition: str | None
    precipitation_mm: float | None
    temp_min_c: float | None
    temp_max_c: float | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ForecastDay:
        return cls(
            date=_parse_date(d.get("date")),
            condition=d.get("condition"),
            precipitation_mm=d.get("precipitation_mm"),
            temp_min_c=d.get("temp_min_c"),
            temp_max_c=d.get("temp_max_c"),
            raw=dict(d.get("raw") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": _iso_or_none(self.date),
            "condition": self.condition,
            "precipitation_mm": self.precipitation_mm,
            "temp_min_c": self.temp_min_c,
            "temp_max_c": self.temp_max_c,
            "raw": dict(self.raw or {}),
        }


@dataclass(frozen=True, slots=True)
class CurrentWeather:
    condition: str | None
    temperature_c: float | None
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CurrentWeather:
        return cls(
            condition=d.get("condition"),
            temperature_c=d.get("temperature_c"),
            raw=dict(d.get("raw") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "temperature_c": self.temperature_c,
            "raw": dict(self.raw or {}),
        }


@dataclass(frozen=True, slots=True)
class Decision:
    can_wash: bool
    score: int
    reason: str
    days_until_wash: int | None
    blocking_days: list[date]
    forecast_summary: list[dict]
    days_analyzed: int

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Decision:
        blocking_raw = d.get("blocking_days") or []
        return cls(
            can_wash=bool(d.get("can_wash", False)),
            score=int(d.get("score", 0)),
            reason=str(d.get("reason", "")),
            days_until_wash=(
                None if d.get("days_until_wash") is None else int(d["days_until_wash"])
            ),
            blocking_days=[_parse_date(x) for x in blocking_raw if x is not None],
            forecast_summary=list(d.get("forecast_summary") or []),
            days_analyzed=int(d.get("days_analyzed", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_wash": self.can_wash,
            "score": self.score,
            "reason": self.reason,
            "days_until_wash": self.days_until_wash,
            "blocking_days": [_iso_or_none(d) for d in self.blocking_days],
            "forecast_summary": list(self.forecast_summary),
            "days_analyzed": self.days_analyzed,
        }


@dataclass(frozen=True, slots=True)
class WashEntry:
    timestamp: str
    source: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WashEntry:
        return cls(
            timestamp=str(d.get("timestamp", "")),
            source=str(d.get("source", "manual")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"timestamp": self.timestamp, "source": self.source}


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    entity_id: str
    success_count: int
    failure_count: int
    last_success_ts: str | None
    last_failure_ts: str | None
    last_error: str | None
    last_seen_ts: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProviderHealth:
        return cls(
            entity_id=str(d.get("entity_id", "")),
            success_count=int(d.get("success_count", 0)),
            failure_count=int(d.get("failure_count", 0)),
            last_success_ts=d.get("last_success_ts"),
            last_failure_ts=d.get("last_failure_ts"),
            last_error=d.get("last_error"),
            last_seen_ts=str(d.get("last_seen_ts", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_success_ts": self.last_success_ts,
            "last_failure_ts": self.last_failure_ts,
            "last_error": self.last_error,
            "last_seen_ts": self.last_seen_ts,
        }


@dataclass(frozen=True, slots=True)
class StoredData:
    wash_log: list[WashEntry] = field(default_factory=list)
    snooze_until: str | None = None
    last_failover_ts: str | None = None
    last_failover_from: str | None = None
    last_failover_to: str | None = None
    provider_health: dict[str, ProviderHealth] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> StoredData:
        return cls(
            wash_log=[],
            snooze_until=None,
            last_failover_ts=None,
            last_failover_from=None,
            last_failover_to=None,
            provider_health={},
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> StoredData:
        if not d:
            return cls.empty()
        wash_log_raw = d.get("wash_log") or []
        health_raw = d.get("provider_health") or {}
        return cls(
            wash_log=[WashEntry.from_dict(e) for e in wash_log_raw],
            snooze_until=d.get("snooze_until"),
            last_failover_ts=d.get("last_failover_ts"),
            last_failover_from=d.get("last_failover_from"),
            last_failover_to=d.get("last_failover_to"),
            provider_health={
                k: ProviderHealth.from_dict(v) for k, v in health_raw.items() if isinstance(v, dict)
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "wash_log": [e.to_dict() for e in self.wash_log],
            "snooze_until": self.snooze_until,
            "last_failover_ts": self.last_failover_ts,
            "last_failover_from": self.last_failover_from,
            "last_failover_to": self.last_failover_to,
            "provider_health": {k: v.to_dict() for k, v in self.provider_health.items()},
        }
