"""Database configuration helpers."""

import re
from urllib.parse import parse_qsl, unquote, urlsplit

from django.core.exceptions import ImproperlyConfigured

_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def _decode_url_component(value: str, label: str) -> str:
    if _INVALID_PERCENT_ESCAPE.search(value):
        raise ImproperlyConfigured(
            f"DATABASE_URL contains invalid percent encoding in {label}."
        )
    try:
        return unquote(value, errors="strict")
    except UnicodeDecodeError as exc:
        raise ImproperlyConfigured(
            f"DATABASE_URL contains invalid UTF-8 encoding in {label}."
        ) from exc


def database_config(database_url: str | None, sqlite_name) -> dict[str, object]:
    """Build Django's default database configuration from the environment."""
    if database_url is None:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": sqlite_name}

    try:
        parsed = urlsplit(database_url)
        port = parsed.port
        if _INVALID_PERCENT_ESCAPE.search(parsed.query):
            raise ValueError("invalid percent encoding in query")
        query_items = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            errors="strict",
        )
    except ValueError as exc:
        raise ImproperlyConfigured(f"Invalid DATABASE_URL: {exc}") from exc

    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ImproperlyConfigured(
            "DATABASE_URL must use the postgres or postgresql scheme."
        )
    if parsed.fragment:
        raise ImproperlyConfigured("DATABASE_URL must not contain a fragment.")
    if not parsed.hostname:
        raise ImproperlyConfigured("DATABASE_URL must include a hostname.")

    database_name = _decode_url_component(
        parsed.path.removeprefix("/"), "database name"
    )
    if not database_name:
        raise ImproperlyConfigured("DATABASE_URL must include a database name.")

    options: dict[str, str] = {}
    for key, value in query_items:
        if key in options:
            raise ImproperlyConfigured(
                f"DATABASE_URL contains duplicate query parameter: {key}"
            )
        options[key] = value

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": database_name,
        "USER": _decode_url_component(parsed.username or "", "username"),
        "PASSWORD": _decode_url_component(parsed.password or "", "password"),
        "HOST": parsed.hostname,
        "PORT": str(port) if port is not None else "",
        "OPTIONS": options,
    }
