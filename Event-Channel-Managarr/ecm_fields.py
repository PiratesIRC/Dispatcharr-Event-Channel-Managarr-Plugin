"""Settings form layout for Dispatcharr 0.34 and later.

Dispatcharr 0.34.0 added collapsible ``section`` fields and a ``rows`` height
for ``text`` fields. Both need ``manifest_version`` 1 in ``plugin.json``, and
Dispatcharr 0.33 and earlier drop a field of any type they do not know, with
only a log warning (0.33.0 ``loader.py`` ``_normalize_fields``). So the
plugin keeps its flat form in ``Plugin.fields``, which every version reads, and
builds the sectioned copy here for ``Plugin.get_fields``, which only 0.34 and
later call. The 0.34 form also draws seven text settings as pick lists and tables
under new ids (``LIST_SETTINGS``, ``TABLE_SETTINGS``); ``legacy_settings`` writes
them back into the old text settings at run time, so one form reaches the code.
"""

import copy

import ecm_parsing

# Dispatcharr 0.34 draws these settings as pick lists and tables. Each one is
# stored under a NEW id, because 0.34's multiselect and table fields need a list
# value and a stored text value would break the form (a multiselect calls .map on
# it). The old text setting keeps its id: 0.33 and earlier still edit it, and
# legacy_settings() writes a new list value back into the old id's text form at
# the start of every run, so every parser and validator reads the text form.
LIST_SETTINGS = {
    # new id: old id
    "channel_profile_list": "channel_profile_name",
    "channel_group_list": "channel_groups",
    "stream_name_group_list": "stream_name_groups",
    "scheduled_time_list": "scheduled_times",
}
TABLE_SETTINGS = {
    # new id: (old id, left column, right column)
    "group_default_event_day_rows": ("group_default_event_day", "group", "day"),
    "group_epg_source_rows": ("group_epg_source_map", "group", "source"),
}
# Hide Rules Priority is drawn as a table with one row per rule: a Rule pick
# list, a Number and, for [PastDate] only, Grace hours. Both number columns are
# pick lists, not number cells: Dispatcharr 0.34.1 refuses a save whose number
# cell is not a number (loader.py _validate_table_value), and its number input
# sends an empty string when a user clears it. A pick list accepts no value.
HIDE_RULES_TABLE = ("hide_rule_rows", "hide_rules_priority")  # (new id, old id)
NEW_ID_BY_OLD_ID = {old: new for new, old in LIST_SETTINGS.items()}
NEW_ID_BY_OLD_ID.update({spec[0]: new for new, spec in TABLE_SETTINGS.items()})
NEW_ID_BY_OLD_ID[HIDE_RULES_TABLE[1]] = HIDE_RULES_TABLE[0]

# Every tag Plugin._check_hide_rule knows, with what its number counts (None:
# the rule takes no number).
HIDE_RULE_TAGS = (
    ("NoEPG", None),
    ("BlankName", None),
    ("WrongDayOfWeek", "days"),
    ("NoEventPattern", None),
    ("EmptyPlaceholder", None),
    ("ShortDescription", "characters"),
    ("ShortChannelName", "characters"),
    ("NumberOnly", None),
    ("PastDate", "days"),
    ("FutureDate", "days"),
    ("PreEvent", "minutes"),
    ("UndatedAge", "days"),
    ("UndatedEnded", "hours"),
    ("InactiveRegex", None),
)
# The value a number cell holds when the rule should use its own default.
DEFAULT_CHOICE = "default"
HIDE_RULE_NUMBERS = tuple(range(0, 61)) + (75, 90, 120, 180, 240, 360, 720, 1440)
HIDE_RULE_HOURS = tuple(range(0, 49)) + (72, 96, 168)

WEEKDAY_NAMES = ecm_parsing.WEEKDAY_NAMES

# Scheduled run times offered in the pick list: every five minutes. A time
# already saved at another minute is added to the list so it still shows.
SCHEDULE_STEP_MINUTES = 5

NEW_FIELDS = {
    "channel_profile_list": {
        "label": "📺 Channel Profiles (Required)",
        "help_text": "REQUIRED. The Dispatcharr channel profiles whose channels this plugin manages. Pick one or more. A channel that has no membership row in one of these profiles is invisible to this plugin and is never scanned. The list is read when the plugin loads: after creating a profile, press the refresh control on the Plugins page to see it here.",
    },
    "channel_group_list": {
        "label": "📂 Channel Groups",
        "help_text": "Narrows the scan to these channel groups inside the profiles above. Leave empty to scan every group in those profiles. The list holds the groups that contain at least one channel, read when the plugin loads: after adding channels to a new group, press the refresh control on the Plugins page to see it here.",
    },
    "stream_name_group_list": {
        "label": "🔀 Stream Name Groups",
        "help_text": "Channel groups that read the stream name whatever Name Source says, for a provider that keeps the channel names fixed and puts each week's events in the stream names. Each group must also be in Channel Groups. If such a group is mapped to its own source in Per-Group EPG Sources, the plugin creates that source set to read the stream name, so the guide follows the stream too. A source that already exists is not changed: set its Name Source to Stream Name in Dispatcharr's EPG source editor. Leave empty to change nothing.",
    },
    "scheduled_time_list": {
        "label": "⏰ Scheduled Run Times",
        "help_text": "Times of day to run the scan automatically, in Dispatcharr's own timezone. Pick one or more; type part of a time, for example 06:, to narrow the list. Leave empty to disable scheduled runs. Click 'Save Schedule' after changing this.",
    },
    "group_default_event_day_rows": {
        "label": "📆 Default Event Day by Group",
        "help_text": "For a group whose names carry a kickoff time but no date and no day word, the day its events are on. Add one row per group. [WrongDayOfWeek] then hides those channels on other days, so the guide does not show them as on today. A day word in the name, including MNF, TNF and SNF, still wins. Pair it with [WrongDayOfWeek:0] in Hide Rules Priority to show them on that day only; plain [WrongDayOfWeek] allows one day either side. The day is judged in Dispatcharr's own timezone. Leave the table empty to change nothing.",
        "columns": {"group": "Channel group", "day": "Day"},
    },
    "hide_rule_rows": {
        "label": "📜 Hide Rules Priority",
        "help_text": "The rules that hide a channel, one per row. A channel that matches any rule in the table is hidden, whatever the order of the rows.",
        "columns": {"rule": "Rule", "number": "Number", "hours": "Grace hours ([PastDate] only)"},
    },
    "group_epg_source_rows": {
        "label": "🗂️ Per-Group EPG Sources",
        "help_text": "Gives a channel group its own dummy EPG source, so groups needing different timezones, durations or title patterns do not have to share one. Add one row per group and type the source name. A group you do not list keeps the shared source, so an empty table changes nothing. The plugin creates a listed source and seeds it from the settings above, then never writes to it again: from that point its timezone, duration, patterns and templates are yours to edit in Dispatcharr's own EPG source editor. The group must also be in scan scope, meaning it is picked in Channel Groups above or that field is empty, or the mapping creates a source and moves nothing. Removing a row moves those channels back to the shared source on the next applied run. Run Validate Configuration after editing this.",
        "columns": {"group": "Channel group", "source": "EPG source name"},
    },
}

# Dispatcharr draws help text as one plain paragraph: no line breaks, no
# Markdown. In the 0.34 form a long help text is cut to its first sentences and
# the rest follows as titled info fields, one topic each, right below the field.
# Keyed by the field id in the 0.34 form. Each value is (short help, [(title,
# paragraph), ...]). The flat form keeps the full text.
HELP_PARAGRAPHS = {
    "hide_rule_rows_about": (
        "The rules that hide a channel, one per row. A channel that matches any rule in the table is hidden, whatever the order of the rows. A rule left out of the table is never applied, and an empty table uses the default rules.",
        [
            ("Order of the rows", "The order decides only which rule is named as the reason in the CSV and the run summary: the first row that matches is the one reported. The table cannot move a row, so to change the order, change the Rule in each row."),
            ("Number", "Leave Number on Default to use the rule's own default. It counts days for [PastDate], [FutureDate], [UndatedAge] and [WrongDayOfWeek], characters for [ShortDescription] and [ShortChannelName], minutes for [PreEvent] and hours for [UndatedEnded]. The defaults are [PastDate] 0, [FutureDate] 14, [UndatedAge] 2, [WrongDayOfWeek] 1, [ShortDescription] 15, [ShortChannelName] 25 and [PreEvent] 15; [UndatedEnded] uses the Undated Event Grace Period setting. The other rules take no number and ignore it."),
            ("Grace hours", "Used only by [PastDate]: the hours after the event ends before the channel is hidden. Default uses the Past Date Grace Period setting. Every other rule ignores this column."),
            ("[PreEvent]", "Applies to a name that carries a date and a clock time: it hides the channel until that many minutes before the event starts, read in the Channel Name Event Timezone. It is not in the default list."),
            ("[UndatedEnded]", "Applies to a name that carries a clock time but no date: it hides the channel once the first-seen date plus that time plus the event duration plus the grace period has passed."),
            ("[WrongDayOfWeek]", "Allows that many days either side of the named day; a Number of 0 means that day only."),
        ],
    ),
    "stream_name_group_list": (
        "Channel groups that read the stream name whatever Name Source says, for a provider that keeps the channel names fixed and puts each week's events in the stream names. Each group must also be in Channel Groups. Leave empty to change nothing.",
        [
            ("The guide for these groups", "If such a group is mapped to its own source in Per-Group EPG Sources, the plugin creates that source set to read the stream name, so the guide follows the stream too. A source that already exists is not changed: set its Name Source to Stream Name in Dispatcharr's EPG source editor."),
        ],
    ),
    "manage_dummy_epg": (
        "Attaches a dummy EPG source, managed by this plugin, to every visible channel that has no EPG assigned. This is the setting that CREATES guide data. '🔌 Auto-Remove EPG on Hide' above only clears it.",
        [
            ("What the guide shows", "The event read out of the channel name during its time window, 'Upcoming at <time>: <event>' before it, and 'Ended at <time>: <event>' after it. When no time can be read from the name, the guide shows the channel name for the whole day instead."),
        ],
    ),
    "override_existing_epg": (
        "Requires '🗓️ Manage Dummy EPG'. Off by default. Turn this on to ALSO replace the guide on a visible channel whose linked EPG source has no programmes in the next 24 hours, which shows as a blank guide.",
        [
            ("Without this setting", "The plugin's guide is attached only to visible channels that have NO EPG at all."),
            ("Placeholder guides", "A guide that has any programme is never replaced, including one that only repeats a placeholder such as 'No Event Today': for that, fill in '🧩 Regex: Placeholder Guide Titles' below, or clear the EPG on those channels. The result of every run names the guides that visible channels kept."),
        ],
    ),
    "group_epg_source_rows_about": (
        "Gives a channel group its own dummy EPG source, so groups needing different timezones, durations or title patterns do not have to share one. Add one row per group and type the source name. A group you do not list keeps the shared source, so an empty table changes nothing.",
        [
            ("Who edits the source", "The plugin creates a listed source and seeds it from the settings above, then never writes to it again: from that point its timezone, duration, patterns and templates are yours to edit in Dispatcharr's own EPG source editor."),
            ("Scan scope", "The group must also be in scan scope, meaning it is picked in Channel Groups above or that field is empty, or the mapping creates a source and moves nothing."),
            ("Removing a row", "Removing a row moves those channels back to the shared source on the next applied run. Run Validate Configuration after editing this table."),
        ],
    ),
}
HELP_SUFFIX = "_help_"

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


def split_list(text):
    """A comma-separated setting as a list of trimmed, non-empty entries."""
    if isinstance(text, (list, tuple)):
        return [str(v).strip() for v in text if str(v).strip()]
    if text is None:
        return []
    return [part.strip() for part in str(text).split(",") if part.strip()]


def _day_name(word):
    """A day word as its full name, or the word unchanged when it is not a day."""
    index = ecm_parsing.WEEKDAY_WORDS.get(str(word).strip().casefold())
    return WEEKDAY_NAMES[index] if index is not None else str(word).strip()


def rows_from_lines(text, left, right):
    """'Left = Right' lines as table rows. A line with no '=' keeps its text in
    the left column, so Validate Configuration can still report it."""
    rows = []
    if text is None:
        return rows
    for line in str(text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        value = value.strip() if sep else ""
        if right == "day" and value:
            value = _day_name(value)
        rows.append({left: name.strip(), right: value})
    return rows


def lines_from_rows(rows, left, right):
    """Table rows as 'Left = Right' lines. A row with both cells empty is
    dropped; a row with one empty cell is kept so Validate reports it."""
    lines = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get(left) or "").strip()
        value = str(row.get(right) or "").strip()
        if not name and not value:
            continue
        lines.append(f"{name} = {value}")
    return "\n".join(lines)


def split_hide_rule_items(text):
    """The tags in a Hide Rules Priority text, split the way
    ``Plugin._parse_hide_rules`` splits them: on newlines when the text has no
    comma, otherwise on commas outside square brackets."""
    if text is None:
        return []
    text = str(text)
    if "\n" in text and "," not in text:
        return [item.strip() for item in text.strip().split("\n") if item.strip()]
    items, current, depth = [], "", 0
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            if current.strip():
                items.append(current.strip())
            current = ""
            continue
        current += char
    if current.strip():
        items.append(current.strip())
    return items


def rows_from_hide_rules(text):
    """Hide Rules Priority text as table rows, in order. An item without square
    brackets is left out, as the rule parser leaves it out. A number the parser
    would reject is kept as typed, so writing the rows back changes nothing."""
    rows = []
    for item in split_hide_rule_items(text):
        if not (item.startswith("[") and item.endswith("]")):
            continue
        name, _, rest = item[1:-1].partition(":")
        number, hours = DEFAULT_CHOICE, DEFAULT_CHOICE
        if rest:
            parts = rest.split(":")
            if len(parts) == 2 and parts[1].endswith("h"):
                number, hours = parts[0], parts[1][:-1]
            else:
                number = rest
        rows.append({"rule": name, "number": number, "hours": hours})
    return rows


def _choice(value):
    """A number cell as text, empty when it holds nothing or Default."""
    text = "" if value is None else str(value).strip()
    return "" if text == DEFAULT_CHOICE else text


def hide_rules_from_rows(rows):
    """Table rows as Hide Rules Priority text. A row with no rule is dropped.
    A number on a rule that takes none is dropped, and Grace hours are written
    only for [PastDate] (a rule this plugin does not know keeps both). An empty
    table gives an empty text, which the rule parser reads as the default list."""
    units = dict(HIDE_RULE_TAGS)
    tags = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("rule") or "").strip()
        if not name:
            continue
        number, hours = _choice(row.get("number")), _choice(row.get("hours"))
        known = name in units
        if hours and (name == "PastDate" or not known):
            tags.append(f"[{name}:{number or 0}:{hours}h]")
        elif number and (not known or units[name] is not None):
            tags.append(f"[{name}:{number}]")
        else:
            tags.append(f"[{name}]")
    return ",".join(tags)


def _number_options(choices, extra=()):
    """Default first, then the numbers, then any other saved value as typed."""
    values = [str(n) for n in choices]
    for value in extra or ():
        value = str(value or "").strip()
        if value and value != DEFAULT_CHOICE and value not in values:
            values.append(value)
    numeric = sorted((v for v in values if v.isdigit()), key=int)
    other = [v for v in values if not v.isdigit()]
    return ([{"label": "Default", "value": DEFAULT_CHOICE}]
            + [{"label": v, "value": v} for v in numeric + other])


def _hide_rule_fields(field, old_value, settings):
    """The info field and table that replace Hide Rules Priority on 0.34.

    A blank saved text shows the default rules, which is what the rule parser
    applies for a blank text. A rule name or number this plugin does not offer,
    saved in the text or the table, is added to its pick list, because
    Dispatcharr refuses a save holding a value that is not an option."""
    new_id = HIDE_RULES_TABLE[0]
    meta = NEW_FIELDS[new_id]
    text = old_value if str(old_value or "").strip() else field.get("default", "")
    default = rows_from_hide_rules(text)
    rows = default + _stored_rows(settings, new_id)
    units = dict(HIDE_RULE_TAGS)
    names = [name for name, _ in HIDE_RULE_TAGS]
    for row in rows:
        name = str(row.get("rule") or "").strip()
        if name and name not in names:
            names.append(name)
    rule_options = [
        {"label": f"[{name}]" + (f" ({units[name]})" if units.get(name) else ""),
         "value": name}
        for name in sorted(names, key=str.casefold)
    ]
    columns = [
        {"id": "rule", "label": meta["columns"]["rule"], "type": "select",
         "options": rule_options},
        {"id": "number", "label": meta["columns"]["number"], "type": "select",
         "options": _number_options(HIDE_RULE_NUMBERS, [r.get("number") for r in rows])},
        {"id": "hours", "label": meta["columns"]["hours"], "type": "select",
         "options": _number_options(HIDE_RULE_HOURS, [r.get("hours") for r in rows])},
    ]
    # A table draws only its label, not its help text, so the explanation goes
    # in an info field above it (split into paragraphs by HELP_PARAGRAPHS).
    return [
        {"id": new_id + ABOUT_SUFFIX, "label": meta["label"], "type": "info",
         "description": meta["help_text"]},
        {"id": new_id, "label": "", "type": "table", "columns": columns,
         "default": default},
    ]


def legacy_settings(settings):
    """Return a copy of ``settings`` with every list or table value written back
    into its old text setting, so the rest of the plugin reads one form.

    Only a new id holding a list counts; anything else leaves the old setting
    alone. Dispatcharr 0.34 fills a new id that was never saved with its field
    default, which get_fields builds from the old setting, so the two agree.
    """
    out = dict(settings or {})
    for new_id, old_id in LIST_SETTINGS.items():
        value = out.get(new_id)
        if isinstance(value, list):
            out[old_id] = ",".join(split_list(value)) if new_id == "scheduled_time_list" \
                else ", ".join(split_list(value))
    for new_id, (old_id, left, right) in TABLE_SETTINGS.items():
        value = out.get(new_id)
        if isinstance(value, list):
            out[old_id] = lines_from_rows(value, left, right)
    new_id, old_id = HIDE_RULES_TABLE
    if isinstance(out.get(new_id), list):
        out[old_id] = hide_rules_from_rows(out[new_id])
    return out


def _options(names, extra=(), forbidden=""):
    """Sorted pick-list options from ``names`` plus every ``extra`` value.

    Names are deduplicated case-insensitively, keeping the first spelling seen.
    A name containing a character in ``forbidden`` is left out, because the text
    form it is written back into could not hold it. An ``extra`` value is never
    left out: Dispatcharr rejects a save holding a value that is not an option.
    """
    seen = {}
    for name in names or ():
        name = str(name or "").strip()
        if name and not any(c in name for c in forbidden):
            seen.setdefault(name.casefold(), name)
    for name in extra or ():
        name = str(name or "").strip()
        if name:
            seen.setdefault(name.casefold(), name)
    return [{"label": n, "value": n} for n in sorted(seen.values(), key=str.casefold)]


def _canonical(values, names):
    """Each value spelled as the matching name (case-insensitive), else as typed."""
    by_key = {str(n).casefold(): str(n) for n in names or ()}
    return [by_key.get(v.casefold(), v) for v in values]


def _schedule_options(extra=()):
    options = []
    known = set()
    for minute in range(0, 24 * 60, SCHEDULE_STEP_MINUTES):
        value = f"{minute // 60:02d}{minute % 60:02d}"
        known.add(value)
        options.append({"label": f"{value[:2]}:{value[2:]}", "value": value})
    for value in extra or ():
        if value not in known:
            known.add(value)
            options.append({"label": value, "value": value})
    return options


def _stored_list(settings, new_id):
    value = (settings or {}).get(new_id)
    return split_list(value) if isinstance(value, list) else []


def _stored_rows(settings, new_id):
    value = (settings or {}).get(new_id)
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def pick_list_fields(flat_fields, settings=None, profile_names=(), group_names=()):
    """Return the flat form with the seven text settings replaced by pick lists
    and tables under their new ids. The input is not modified.

    ``settings`` are the saved settings; a field's default is built from the old
    text setting, so an install that never saved the new field keeps its values.
    ``profile_names`` and ``group_names`` fill the option lists.
    """
    settings = settings or {}
    out = []
    for field in flat_fields or []:
        if not isinstance(field, dict) or field.get("id") not in NEW_ID_BY_OLD_ID:
            out.append(copy.deepcopy(field) if isinstance(field, dict) else field)
            continue
        old_id = field["id"]
        new_id = NEW_ID_BY_OLD_ID[old_id]
        meta = NEW_FIELDS[new_id]
        old_value = settings.get(old_id, field.get("default", ""))

        if new_id == HIDE_RULES_TABLE[0]:
            out.extend(_hide_rule_fields(field, old_value, settings))
            continue

        if new_id in LIST_SETTINGS:
            default = split_list(old_value)
            stored = _stored_list(settings, new_id)
            if new_id == "scheduled_time_list":
                options = _schedule_options(default + stored)
            else:
                names = profile_names if new_id == "channel_profile_list" else group_names
                default = _canonical(default, names)
                options = _options(names, default + stored, forbidden=",")
            out.append({
                "id": new_id, "label": meta["label"], "type": "multiselect",
                "default": default, "options": options, "help_text": meta["help_text"],
            })
            continue

        left, right = TABLE_SETTINGS[new_id][1], TABLE_SETTINGS[new_id][2]
        default = rows_from_lines(old_value, left, right)
        for row in default:
            row[left] = _canonical([row[left]], group_names)[0]
        stored = _stored_rows(settings, new_id)
        used_groups = [r.get(left) for r in default + stored if r.get(left)]
        columns = [{"id": left, "label": meta["columns"][left], "type": "select",
                    "options": _options(group_names, used_groups, forbidden="=\n")}]
        if right == "day":
            used_days = [r.get(right) for r in default + stored if r.get(right)]
            columns.append({"id": right, "label": meta["columns"][right], "type": "select",
                            "options": _options(WEEKDAY_NAMES, used_days)})
            # Keep Monday to Sunday order rather than alphabetical.
            order = {name: i for i, name in enumerate(WEEKDAY_NAMES)}
            columns[-1]["options"].sort(key=lambda o: order.get(o["value"], 99))
        else:
            columns.append({"id": right, "label": meta["columns"][right], "type": "string"})
        # A table draws only its label, not its help text, so the explanation
        # goes in an info field above it and the table itself has no label.
        out.append({"id": new_id + ABOUT_SUFFIX, "label": meta["label"], "type": "info",
                    "description": meta["help_text"]})
        out.append({"id": new_id, "label": "", "type": "table", "columns": columns,
                    "default": default})
    return out


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
        out.extend(_split_help(item, current))
    return out


def _split_help(item, section):
    """Cut a long help text to its short form (in place) and return the rest as
    titled info fields that follow the field. An info field's text is in
    ``description``; any other field's is in ``help_text``."""
    spec = HELP_PARAGRAPHS.get(item.get("id"))
    if not spec:
        return []
    short, paragraphs = spec
    item["description" if item.get("type") == "info" else "help_text"] = short
    extra = []
    for number, (title, text) in enumerate(paragraphs, start=1):
        info = {"id": f"{item['id']}{HELP_SUFFIX}{number}", "label": title,
                "type": "info", "description": text}
        if section is not None:
            info["section"] = section
        extra.append(info)
    return extra
