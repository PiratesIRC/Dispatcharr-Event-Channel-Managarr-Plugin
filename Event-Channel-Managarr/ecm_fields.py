"""Settings form layout for Dispatcharr 0.34 and later.

Dispatcharr 0.34.0 added collapsible ``section`` fields and a ``rows`` height
for ``text`` fields. Both need ``manifest_version`` 1 in ``plugin.json``, and
Dispatcharr 0.33 and earlier draw an unknown field type as a text box. So the
plugin keeps its flat form in ``Plugin.fields``, which every version reads, and
builds the sectioned copy here for ``Plugin.get_fields``, which only 0.34 and
later call. Nothing here changes a setting id, type or default: a stored value
reads the same under both forms.
"""

import copy

# The ``info`` fields that head each group of settings in the flat form.
SECTION_PREFIX = "_section_"

# Groups a typical user rarely opens start closed.
COLLAPSED_SECTIONS = frozenset((
    "_section_duplicates",
    "_section_scheduling",
    "_section_advanced",
    "_section_usage",
))

# Starting height, in lines, of text fields that usually hold one short value.
TEXT_ROWS = {
    "hide_rules_priority": 3,
    "regex_channels_to_ignore": 2,
    "regex_mark_inactive": 2,
    "regex_force_visible": 2,
    "placeholder_guide_titles": 2,
}

# Id suffix of the info field that keeps a section header's explanation.
ABOUT_SUFFIX = "_about"


def is_section_header(field):
    """True for an ``info`` field whose id marks the start of a settings group."""
    return (isinstance(field, dict) and field.get("type") == "info"
            and str(field.get("id", "")).startswith(SECTION_PREFIX))


def sectioned_fields(flat_fields):
    """Return the flat form rearranged into collapsible sections.

    Each section header becomes a ``section`` marker with the same id, and every
    field after it, up to the next header, gets ``"section": <that id>``. A
    header's explanation moves to an ``info`` field at the top of its section,
    because a marker shows only its label. Fields before the first header stay
    ungrouped (Dispatcharr draws ungrouped fields above every section). The
    input is not modified.
    """
    out = []
    current = None
    for field in flat_fields or []:
        if not isinstance(field, dict):
            continue
        if is_section_header(field):
            current = field["id"]
            out.append({
                "id": current,
                "label": field.get("label") or current,
                "type": "section",
                "collapsed": current in COLLAPSED_SECTIONS,
            })
            text = field.get("description") or field.get("help_text")
            if text:
                out.append({
                    "id": current + ABOUT_SUFFIX,
                    "label": "",
                    "type": "info",
                    "description": text,
                    "section": current,
                })
            continue
        item = copy.deepcopy(field)
        if current is not None:
            item["section"] = current
        if item.get("type") == "text" and item.get("id") in TEXT_ROWS:
            item["rows"] = TEXT_ROWS[item["id"]]
        out.append(item)
    return out
