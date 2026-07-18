"""Pure decision logic for bootstrap_ecm.py. No I/O, no ORM, no Django.

Split out so the one thing in this slice that writes to Postgres and /data has
its behavior pinned by unit tests rather than by reading the code.
"""

PLACEHOLDER_PREFIX = "REPLACE_ME"

CREDENTIAL_ENV = (
    ("ECM_DISPATCHARR_URL", "dispatcharr_url"),
    ("ECM_DISPATCHARR_USERNAME", "dispatcharr_username"),
    ("ECM_DISPATCHARR_PASSWORD", "dispatcharr_password"),
)


def is_placeholder(value):
    return isinstance(value, str) and value.startswith(PLACEHOLDER_PREFIX)


def merge_settings(existing, template, env):
    """Return (merged, changed).

    Rules:
      - a template value that is a REPLACE_ME placeholder NEVER overwrites an
        existing value; it only fills an absent key
      - runtime-only keys already present are preserved
      - credentials come from `env` only, and an existing credential is kept
        when the env does not supply one
    """
    existing = dict(existing or {})
    merged = dict(existing)

    for key, value in (template or {}).items():
        if is_placeholder(value) and key in merged:
            continue
        merged[key] = value

    for env_name, key in CREDENTIAL_ENV:
        value = (env or {}).get(env_name)
        if value:
            merged[key] = value

    return merged, merged != existing
