"""IANA timezone validation shared by feature schemas."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Timezone is invalid") from error
    return value
