# Event Channel Managarr documentation

Four pages, split by who is reading.

## If you are running Dispatcharr

**[User guide](USER-GUIDE.md)** is the one you want. It covers every setting and
action, how the hide rules decide what to hide and in what order, how far ahead an
event channel should appear, what the managed dummy EPG can and cannot show, how to
point Jellyfin, Plex or Emby at the right URLs, where every file is written, the CSV
format, and a troubleshooting section arranged by symptom.

Three sections in there answer most first questions:

- **Hide Rule Logic** explains why a channel with tomorrow's game in its name is
  visible on purpose, and which tag to change if you want day-of-event only.
- **Client Setup** covers the mistake that costs the most time: pointing a client's
  guide provider at the M3U playlist URL instead of the XMLTV one, and using the
  unscoped output URLs, which do not exclude hidden channels.
- **Troubleshooting** is arranged by what you are seeing rather than by feature.

**[Changelog](CHANGELOG.md)** indexes every released version with a link to its
release notes.

## If you are working on Event Channel Managarr itself

**[Development notes](DEVELOPMENT.md)** cover the runtime model first, because it
governs everything else: the plugin runs inside Dispatcharr's Django backend, so
there is no build, no standalone run and no staging. They also cover the code map,
deploying to a container, the test suite and what each part of it pins, adding a
setting or action, settings precedence, the release procedure, and how to
contribute to this repository and to the plugin marketplace.

---

The **[project front page](../README.md)** describes what the plugin is and what it
does.
