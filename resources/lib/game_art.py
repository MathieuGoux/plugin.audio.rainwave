import difflib
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .constants import USER_AGENT
from .utils import log

# SteamGridDB (https://www.steamgriddb.com) is a community-run art
# database built for exactly this job -- launchers/frontends (Playnite,
# ES-DE, etc) use it the same way this module does: look a game up by
# name, get back purpose-made artwork. It's free, doesn't require a
# game-specific ID up front (the autocomplete search takes a plain
# name), and its "Hero" asset category is specifically wide banner art
# meant to sit behind a game's page -- exactly the shape a fullscreen
# Kodi background needs. Grids (a *different* endpoint) are
# boxart-shaped cover thumbnails and would look wrong stretched across
# 1920x1080 -- heroes and grids are separate asset types with their
# own endpoints and their own valid dimension sets; asking /grids for
# hero-sized dimensions is a 400, not an empty result.
API_BASE = "https://www.steamgriddb.com/api/v2"
HERO_DIMENSIONS = "1920x620,3840x1240"
REQUEST_TIMEOUT = 8  # seconds

MANIFEST_NAME = "manifest.json"
CACHE_SUBDIR = "art_cache"

# Bump this whenever a change could make previously-cached *failures*
# wrong (e.g. an endpoint/parameter bug that made real matches 400 out
# as "no art found", or a matching heuristic change that would now
# find something a stricter previous version didn't). A mismatch wipes
# the manifest so nothing from before the change keeps incorrectly
# blocking retries for FAILED_TTL -- successful lookups are safe
# either way since they get re-verified for real the next time that
# title comes up.
#   2: fixed /heroes vs /grids endpoint mixup (2.5.0 queried /grids
#      with hero-only dimensions, which SteamGridDB 400'd, and that
#      400 was being cached as "no match").
#   3: added the fuzzy title-variant fallback below -- titles that
#      failed outright before (no exact match) may resolve now.
#   4: added the song-title hint fallback below -- albums that still
#      failed even after fuzzy variants may resolve now.
#   5: song-title hint extraction is no longer bracket-only (also
#      catches bare "from Game" and delimiter-separated titles like
#      "Song / Game"), and every variant now gets an ASCII-folded
#      counterpart tried alongside it for titles with accented
#      characters -- either can newly resolve something that failed
#      before.
#   6: album titles now also get subtitle stripping (": Subtitle" /
#      ". Subtitle"), an explicit from/bracket hint, and truncation
#      from the front as well as the end -- covers cases like "Theme
#      from Super Meat Boy" or a colon-separated subtitle mismatch
#      that none of the previous variants could reach. "Live" was
#      also removed from the non-game-hint denylist.
#   7: added a weaker "of"/"for" connector hint (e.g. "The Life and
#      Times of Final Fantasy IX"), front/back truncation is now
#      interleaved instead of exhausting the end before the front
#      ever got a turn, and the song-title fallback now also tries
#      the song title's own words when no bracket/from/delimiter hint
#      is present at all (e.g. "Super Mario Extravaganza!") -- all
#      three can newly resolve titles that failed outright before.
#   8: added stripping of a trailing "-Descriptor-" dash-wrapped
#      segment (e.g. "Romancing SaGa -Minstrel Song-"), which sits in
#      the *middle* of what a "from"/bracket hint captures rather
#      than at either end -- can newly resolve titles that failed
#      outright, or previously relied on a much weaker/later variant
#      to accidentally reach the same answer.
#   9: added a general "[Style word] [Version-like noun]" suffix
#      pattern (e.g. "Okami Jazz Version" -> "Okami") -- previously
#      MIN_TRUNCATED_WORDS blocked blind truncation from ever
#      reaching a legitimate single-word game name in cases like this,
#      since the style word wasn't in the fixed _STRIP_SUFFIXES list.
CACHE_SCHEMA_VERSION = 9

# How long to remember "no match / no art found for this title" before
# letting a future lookup try again. Long enough that a title with no
# real match doesn't get hammered every time it comes up in rotation
# (Rainwave's library repeats constantly), short enough that a game
# added to SteamGridDB after our first attempt eventually gets found.
FAILED_TTL = 7 * 24 * 60 * 60  # 7 days

# How many hero images to keep per game, when SteamGridDB has several
# -- lets the slideshow rotate between a few pieces of art for a game
# that's airing several songs in a row, instead of one static image.
# Overridden per-instance by the "Background images per game" setting
# (see IMAGES_PER_GAME_OPTIONS below); this is just the fallback used
# if that setting is ever missing/out of range.
MAX_IMAGES_PER_GAME = 4

# Index -> actual value for the "Background images per game" enum
# setting (values="1|2|3|4|All"). None means "no client-side cap" --
# whatever SteamGridDB's own /heroes endpoint returns for that game,
# which in practice tops out somewhere in the dozens even for very
# popular games, not literally unbounded.
IMAGES_PER_GAME_OPTIONS = [1, 2, 3, 4, None]

# Below this many images, the matched game alone isn't considered to
# have "enough" art -- see _fill_from_series_siblings() -- and it's
# worth spending a couple of extra API calls trying to round it out
# with art from the rest of its series, if it looks like it has one.
MIN_IMAGES_BEFORE_SERIES_LOOKUP = 2
MAX_SERIES_SIBLINGS = 3
MAX_IMAGES_PER_SIBLING = 2

# How often get()'s cache-hit path is allowed to write last_used_at
# changes to disk. get() runs roughly once a second whenever auto mode
# is showing something, and every hit updates the in-memory recency
# used for LRU eviction below -- persisting that to disk on literally
# every call would mean near-continuous disk writes for no real
# benefit (losing a few minutes of recency data to an unclean
# shutdown doesn't meaningfully change what gets evicted later).
MANIFEST_SAVE_INTERVAL = 5 * 60  # seconds

# Rainwave's "album" field is often an arrangement/compilation *album*
# title, not the literal game name it's talking about -- e.g. "Super
# Mario Bros Remix" or "Final Fantasy Reinvented" won't autocomplete-
# match anything on SteamGridDB, because neither is a real game.
# _title_variants() below exploits a handful of common shapes this
# takes to build a short list of alternate search queries to fall back
# through when the exact title comes up empty. It's a heuristic, not a
# real fuzzy-search API (SteamGridDB's search itself is closer to
# substring matching than typo-tolerant fuzzy matching), so it won't
# catch everything, but it resolves the common cases cheaply.
_STRIP_SUFFIXES = re.compile(
    r"\s*[:\-]?\s*("
    r"Rearranged|Remastered|Reimagined|Reinvented|Revisited|Rebooted|"
    r"Reawakened|Reloaded|Reborn|Renewed|Redux|Unwound|Unleashed|"
    r"Evolved|Arrangement|Arranged|Compilation|Anthology|Tribute|"
    r"Anniversary( Edition)?|Definitive( Edition)?|"
    r"Re[- ]?[Mm]ix(ed)?|[Mm]ix(ed)?"
    r")\s*$",
    re.IGNORECASE,
)

# "[Style word] [Version-like noun]" -- e.g. "Jazz Version", "Piano
# Arrangement", "Rock Cover", "Orchestral Suite" -- a shape rather
# than a fixed word list (unlike _STRIP_SUFFIXES above), since the
# style word varies too much to enumerate (jazz, piano, rock, chip,
# 8-bit, orchestral, acoustic, symphonic, and so on indefinitely).
# Without this, something like "Okami Jazz Version" never reduces
# down to the actual game name at all: "Jazz Version" doesn't match
# any of the specific words in _STRIP_SUFFIXES, and MIN_TRUNCATED_WORDS
# below stops blind word-by-word truncation from ever reaching a
# single-word result like "Okami" on its own, even though it's exactly
# the right one here. Structured suffix stripping like this one isn't
# subject to that floor -- it strips a whole recognized *unit* in one
# step, not one word at a time -- so "Okami" still comes out the other
# end correctly.
_STYLE_SUFFIX = re.compile(
    r"\s*[:\-]?\s*[A-Za-z]+\s+(Version|Arrangement|Cover|Suite|Medley|Rendition|Style|Take|Edit)\s*$",
    re.IGNORECASE,
)

# A trailing "-Descriptor-" segment, dash on both sides -- a fairly
# common convention (particularly in Japanese game OST naming, often
# carried through untranslated into arrangement album titles/hints)
# for tacking a specific version/arrangement name onto the end of a
# title, e.g. "Romancing SaGa -Minstrel Song-". Unlike _STRIP_SUFFIXES
# this isn't a fixed word list -- it's a shape (dash ... dash at the
# very end, with no dash in between) -- since the descriptor itself
# varies too much to enumerate. A real game title ending in a bare
# "-word-" pair is rare enough that stripping this is safe in
# practice.
_DASH_WRAPPED_SUFFIX = re.compile(r"\s*-[^-]+-\s*$")

# A "(from Game)" / "(Game)" / bare "from Game" hint, wherever it
# appears in a title -- shared by _title_variants() below (applied to
# album titles, where the real game name sometimes sits at the very
# end, e.g. "Theme from Super Meat Boy", which plain trailing-word
# truncation could never isolate on its own since it only trims from
# the end) and _extract_game_hints() further down (applied to song
# titles, which layers a couple of extra, riskier delimiter-based
# conventions on top -- see there).
_BRACKETED_HINT = re.compile(
    r"[\(\[]\s*(?:from\s+)?([^\(\)\[\]]+?)\s*[\)\]]\s*$",
    re.IGNORECASE,
)
_BARE_FROM_HINT = re.compile(r"\bfrom\s+(.+?)\s*$", re.IGNORECASE)

# A softer version of the hint above, using "of"/"for" as the
# connector instead of the much more specific "from" -- e.g. "The Life
# and Times of Final Fantasy IX", "Themes for Chrono Trigger".
# Meaningfully less reliable (both words are common enough to show up
# for reasons unrelated to naming a source game -- "Ocarina of Time"
# is itself part of a real game's name), which is why it's tried only
# after the stronger "from"/bracket hint has already had its chance --
# see _title_variants() and _extract_game_hints().
_WEAK_CONNECTOR_HINT = re.compile(r"\b(?:of|for)\s+(.+?)\s*$", re.IGNORECASE)

# A hint is sometimes a style/arrangement descriptor rather than a
# game name -- "(Piano Arrangement)", "(Remix)" -- which would
# otherwise get treated as one and searched as such. Full match only
# (not a substring check): a hint like "Piano Collection of Chrono
# Trigger" should still pass through untouched. Deliberately does NOT
# include "Live" -- unlike the others here, "Live" is common enough as
# an actual, meaningful part of real game titles that excluding it
# outright risked throwing away genuine hints more often than it
# caught false ones.
_NON_GAME_HINTS = re.compile(
    r"^(re)?mix(ed)?$|"
    r"^cover( version)?$|^acoustic( version)?$|"
    r"^orchestral$|^orchestrated$|"
    r"^piano (arrangement|version|cover)$|^instrumental$|"
    r"^demo$|^extended( mix)?$|^acapella$|"
    r"^arrangement$|^remaster(ed)?( version)?$|"
    r"^tribute$|^medley$|^mashup$|^megamix$",
    re.IGNORECASE,
)

# Below this many words, further truncation stops being "drop a
# descriptor word" and starts being "guess at a completely different,
# much more generic game" -- e.g. truncating "Chrono Trigger" to
# "Chrono" risks matching some other, wrong game entirely.
MIN_TRUNCATED_WORDS = 2

# Total alternate queries to try (including the original) before
# giving up -- keeps a title with no real match from generating an
# unbounded number of API calls. There's now enough going on in
# _title_variants() (subtitle stripping, a from/bracket hint,
# diacritic folding, truncation from *both* ends) that this needed
# raising from the original 5 to give the later, lower-confidence
# strategies a realistic chance of getting a turn.
MAX_VARIANTS = 8


def _strip_diacritics(text):
    """ASCII-fold accented characters (e.g. "e" for "e" with an acute
    accent) via Unicode decomposition. SteamGridDB's own search
    doesn't reliably match across accented/unaccented spellings --
    "Ragnarok Online" needed to be searched without the O-umlaut to
    find a match at all, even though the game's real, correct name
    does have one (some entries are just spelled plainly, or
    misspelled, by whoever submitted them) -- so every variant below
    gets an ASCII-folded counterpart added alongside it.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def _strip_subtitle(title):
    """Drop a ": Subtitle" or ". Subtitle" tail, or None if neither
    applies. Rainwave's album title and SteamGridDB's listed name
    don't always agree on how much of a subtitle to include -- e.g.
    "Dragon Quest III" vs. SteamGridDB's "Dragon Quest III: The Seeds
    of Salvation" (or the reverse) -- and colon-separated subtitles
    are common enough in game titles generally that it's worth trying
    the short form even when the query wasn't actually truncated for
    any other reason.

    Colon is unambiguous and always tried. Period is the riskier of
    the two -- title-case abbreviations like "Dr. Mario" or
    "F.E.A.R." also contain periods -- so it's only used if there are
    still at least two words left before it, long enough to read as
    an actual subtitle break rather than an abbreviation.
    """
    for sep in (":", "."):
        if sep not in title:
            continue
        prefix = title.split(sep, 1)[0].strip()
        if prefix and prefix != title and (sep == ":" or len(prefix.split()) >= 2):
            return prefix
    return None


# Roman numerals covering the range real game sequels actually use in
# practice (I-XX) -- an explicit list rather than a generative pattern
# is easier to get right and to reason about here. Order doesn't
# matter for correctness: _TRAILING_NUMBER anchors to the end of the
# string, so a shorter alternative (e.g. "XI") can't win over the
# correct longer one (e.g. "XIX") by leaving characters unconsumed
# before that anchor.
_ROMAN_NUMERALS = (
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
)
_TRAILING_NUMBER = re.compile(
    rf"^(.*\S)\s+(?:\d{{1,2}}|{'|'.join(_ROMAN_NUMERALS)})\s*$"
)


def _series_base_name(name):
    """Strip a trailing sequel number (arabic or roman numeral) or a
    ": Subtitle"/". Subtitle" tail from a resolved game name, giving
    the base series name to look for siblings under -- e.g. "Last
    Bible III" -> "Last Bible", "Dragon Quest III: The Seeds of
    Salvation" -> "Dragon Quest III" (via the subtitle stripper; a
    second pass on that result would also strip the "III", but one
    pass is enough for what this is used for -- see
    _fill_from_series_siblings()). Returns None if nothing
    recognizable to strip, meaning the name doesn't look like part of
    a numbered/subtitled series in the first place.
    """
    subtitle_stripped = _strip_subtitle(name)
    if subtitle_stripped and subtitle_stripped != name:
        return subtitle_stripped

    match = _TRAILING_NUMBER.match(name)
    if match:
        base = match.group(1).strip()
        if base and base != name:
            return base

    return None


def _extract_from_hint(title):
    """Pull a "(from Game)" / "(Game)" / bare "from Game" hint out of
    `title`, or None. See the comment above _BRACKETED_HINT.
    """
    for pattern in (_BRACKETED_HINT, _BARE_FROM_HINT):
        match = pattern.search(title)
        if match:
            hint = match.group(1).strip().strip("()[] ")
            if hint and not _NON_GAME_HINTS.match(hint):
                return hint
    return None


def _extract_weak_hint(title):
    """Same idea as _extract_from_hint(), but for the softer "of"/
    "for" connector -- see the comment above _WEAK_CONNECTOR_HINT for
    why it's kept separate and lower-priority.
    """
    match = _WEAK_CONNECTOR_HINT.search(title)
    if match:
        hint = match.group(1).strip().strip("()[] ")
        if hint and not _NON_GAME_HINTS.match(hint):
            return hint
    return None


def _title_variants(title):
    """Build an ordered, deduplicated list of search queries to try
    for a game title, roughly most-to-least confident: the title
    as-is, a subtitle-stripped form, known trailing modifier words
    stripped, an explicit "from Game" hint if the title has one, then
    progressively shorter truncations from the end *and* the front --
    each with an ASCII-folded counterpart added right next to it
    wherever accents make one meaningfully different (see
    _strip_diacritics()).
    """
    variants = []

    def add(v):
        v = v.strip()
        if not v or v in variants:
            return
        variants.append(v)
        folded = _strip_diacritics(v)
        if folded != v and folded not in variants:
            variants.append(folded)

    add(title)

    subtitle_stripped = _strip_subtitle(title)
    if subtitle_stripped:
        add(subtitle_stripped)

    cleaned = title
    while True:
        stripped = _STRIP_SUFFIXES.sub("", cleaned).strip()
        if stripped == cleaned:
            stripped = _STYLE_SUFFIX.sub("", cleaned).strip()
        if stripped == cleaned:
            stripped = _DASH_WRAPPED_SUFFIX.sub("", cleaned).strip()
        if not stripped or stripped == cleaned:
            break
        cleaned = stripped
    add(cleaned)

    subtitle_stripped = _strip_subtitle(cleaned)
    if subtitle_stripped:
        add(subtitle_stripped)

    hint = _extract_from_hint(cleaned)
    if hint:
        add(hint)

    weak_hint = _extract_weak_hint(cleaned)
    if weak_hint and weak_hint != hint:
        add(weak_hint)

    words = cleaned.split()

    # Interleaved, not "every trailing truncation, then every leading
    # one": a title like "The Life and Times of Final Fantasy IX" has
    # its real game name sitting right at the end, but exhausting the
    # whole trailing-truncation budget first (dropping "IX", then
    # "Fantasy", then "Final"...) would burn through every available
    # slot on useless fragments before leading truncation ever got a
    # turn to approach it from the other direction. Interleaving
    # means both directions make progress within the same budget.
    trailing = list(words)
    leading = list(words)
    while len(variants) < MAX_VARIANTS and (
        len(trailing) > MIN_TRUNCATED_WORDS or len(leading) > MIN_TRUNCATED_WORDS
    ):
        if len(trailing) > MIN_TRUNCATED_WORDS:
            trailing = trailing[:-1]
            add(" ".join(trailing))
            if len(variants) >= MAX_VARIANTS:
                break
        if len(leading) > MIN_TRUNCATED_WORDS:
            leading = leading[1:]
            add(" ".join(leading))

    return variants[:MAX_VARIANTS]


def _best_candidate(candidates, reference):
    """Of a batch of search results, pick whichever's name is
    textually closest to `reference` -- SteamGridDB's own ranking for
    a query isn't always name-similarity-first, so this re-ranks
    locally. `reference` should be the same variant that was actually
    searched with (not necessarily the raw original title): scoring
    "Super Mario Bros. 3" against a still-noisy "Super Mario Bros
    Remix" barely distinguishes it from "Super Mario Bros." (both
    score similarly close due to shared length/prefix), whereas
    scoring against the already-cleaned "Super Mario Bros" gives a
    much sharper, more correct signal.
    """
    def score(candidate):
        name = candidate.get("name", "")
        return difflib.SequenceMatcher(None, name.lower(), reference.lower()).ratio()

    return max(candidates, key=score)


# Slash, tilde, pipe, en/em dash, or a spaced hyphen -- deliberately
# *not* a bare unspaced hyphen, which is far too likely to just be
# part of a compound word in the song title itself (e.g. "Hard-Boiled").
# Song titles only: album titles don't get this one, since it's a
# riskier convention that's really only established for how remix
# communities format individual *track* names.
_HINT_SEGMENT_SPLIT = re.compile(r"\s+(?:/|~|\||\u2013|\u2014|-{1,2})\s+")

# Cap on how many song-title-derived queries get tried in total (across
# every hint and every hint's own _title_variants() cascade, plus the
# raw-song-title fallback below) -- this tier is already several
# heuristics deep, each with its own fuzzy expansion, so left uncapped
# a single odd title could otherwise generate a couple dozen API calls
# for what's ultimately still just a guess.
MAX_SONG_TITLE_VARIANTS = 8


def _extract_game_hints(song_title):
    """Return a list of candidate game-name hints pulled out of a song
    title, most-likely-first -- or an empty list if nothing looks
    extractable. Track titles in remix/arrangement communities very
    often name the source game explicitly somewhere in the title --
    e.g. "Battle BGM Remix (from Final Fantasy VII)", "Battle BGM
    remix from Final Fantasy VII", "BGM 2 / Super Meat Boy" -- which
    names the real game far more reliably than the *album/compilation*
    title does (e.g. "Battle Music Remixes vol. 3", which isn't a real
    game at all). None of the patterns here are reliable on their own
    -- a title might just happen to contain the word "from", or a
    " - " that's part of the song name rather than a separator --
    which is exactly why this whole thing is only tried as a
    last-resort fallback, once every album-title-based variant has
    already failed (see _resolve_game_id()): a wrong guess here just
    means one more failed search, not a wrong picture on screen.
    """
    hints = []

    def add(h):
        h = (h or "").strip().strip("()[] ")
        if h and not _NON_GAME_HINTS.match(h) and h not in hints:
            hints.append(h)

    hint = _extract_from_hint(song_title)
    if hint:
        add(hint)

    weak_hint = _extract_weak_hint(song_title)
    if weak_hint:
        add(weak_hint)

    # Delimiter-separated segments -- try the last segment first
    # (Rainwave/OCR-style titles that use this convention most often
    # put the source game at the end, e.g. "BGM 2 / Super Meat Boy"),
    # then the first segment as a weaker guess for the reverse case.
    segments = [s.strip() for s in _HINT_SEGMENT_SPLIT.split(song_title) if s.strip()]
    if len(segments) > 1:
        add(segments[-1])
        add(segments[0])

    return hints


def _song_title_variants(song_title):
    """Fallback search queries derived from the song title -- first
    from any explicit hints (see _extract_game_hints()), then from
    the song title's own words. That second part matters: plenty of
    song titles just plainly mention the game in prose, with no
    bracket, "from", or delimiter to key off at all (e.g. "Super Mario
    Extravaganza!") -- without it, a title like that would fall
    through this whole tier with nothing tried at all, even though the
    same truncate-from-both-ends approach _title_variants() already
    uses for album titles stands a real chance of isolating "Super
    Mario" from it.
    """
    if not song_title:
        return []

    variants = []

    def extend(new_variants):
        for v in new_variants:
            if v not in variants:
                variants.append(v)
        return len(variants) >= MAX_SONG_TITLE_VARIANTS

    for hint in _extract_game_hints(song_title):
        if extend(_title_variants(hint)):
            return variants[:MAX_SONG_TITLE_VARIANTS]

    extend(_title_variants(song_title))

    return variants[:MAX_SONG_TITLE_VARIANTS]


# How long to back off after a 429 if SteamGridDB's response doesn't
# include a Retry-After header (or it's unparseable) -- a reasonable
# default cooldown rather than guessing something too short and
# tripping the limit again almost immediately.
DEFAULT_RATE_LIMIT_BACKOFF = 60  # seconds


def _parse_retry_after(header_value):
    """Parse a Retry-After header value -- either a plain number of
    seconds (the common case for API rate limits) or an HTTP-date --
    into a number of seconds to wait. Falls back to
    DEFAULT_RATE_LIMIT_BACKOFF if the header is missing or malformed.
    """
    if not header_value:
        return DEFAULT_RATE_LIMIT_BACKOFF
    header_value = header_value.strip()
    if header_value.isdigit():
        return int(header_value)
    try:
        target = parsedate_to_datetime(header_value)
        if target.tzinfo is None:
            # RFC 7231 HTTP-dates are always GMT, but some servers omit
            # the explicit tzinfo that parsedate_to_datetime would
            # otherwise attach -- assume GMT rather than treating it
            # as naive-local and getting the delta wrong.
            target = target.replace(tzinfo=timezone.utc)
        delta = (target - datetime.now(timezone.utc)).total_seconds()
        return max(1, int(delta))
    except Exception:
        return DEFAULT_RATE_LIMIT_BACKOFF


def _cache_key(game_title):
    """Filesystem/dict-safe key for a game title."""
    normalized = re.sub(r"[^a-z0-9]+", "-", game_title.strip().lower()).strip("-")
    # The hash suffix guards against two different titles normalizing
    # to the same slug (e.g. punctuation-only differences), and keeps
    # the key well-formed even for titles that are mostly non-ASCII
    # (common in Rainwave's library) and would otherwise normalize to
    # nothing.
    digest = hashlib.sha1(game_title.encode("utf-8")).hexdigest()[:10]
    return f"{normalized}-{digest}" if normalized else digest


class GameArtProvider:
    """Looks up and caches background art for a game title.

    get(title) is the only method the slideshow needs to call, once a
    tick: it's non-blocking and always returns immediately, either
    with whatever's already cached (a list of local file paths) or an
    empty list. A cache miss quietly kicks off a background thread to
    do the actual lookup+download; the *next* call to get() for that
    title, sometime after the thread finishes, is what picks up the
    result. Nothing here ever blocks the caller waiting on network.

    Everything resolved is written to disk under the addon's profile
    folder (not the addon's own read-only install folder) alongside a
    small manifest.json recording title -> cached files, so a Kodi
    restart doesn't re-fetch the whole library from scratch -- only
    genuinely new titles hit the network at all.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._pending = set()  # cache keys currently being fetched
        self._index = {}  # cache key -> manifest entry (see _load_manifest)
        self._cache_dir = None
        self._api_key = ""
        self._cache_limit_mb = 0
        self._last_manifest_save = 0.0
        # Rate-limit backoff: see _handle_http_error(). 0 means "not
        # currently backing off".
        self._rate_limited_until = 0.0
        # Missing-key nudge: see get(). Fires at most once per
        # GameArtProvider instance (i.e. once per addon service
        # process lifetime, since service.py builds exactly one) --
        # not persisted across Kodi restarts, so it'll nudge again on
        # a fresh session if the key is still missing then, but won't
        # repeat itself while Kodi keeps running.
        self._warned_no_key = False
        self._settings_loaded = False
        self._max_images_per_game = MAX_IMAGES_PER_GAME
        self.reload_settings()

    def reload_settings(self):
        addon = xbmcaddon.Addon()
        new_api_key = addon.getSettingString("steamgriddb_api_key").strip()
        new_cache_limit_mb = addon.getSettingInt("art_cache_limit_mb")

        # Doesn't need the relevant_changed/manifest-reload dance below
        # -- it only affects future fetches, nothing already on disk,
        # so there's no reason not to just always pick up the current
        # value.
        images_index = addon.getSettingInt("art_images_per_game")
        self._max_images_per_game = (
            IMAGES_PER_GAME_OPTIONS[images_index]
            if 0 <= images_index < len(IMAGES_PER_GAME_OPTIONS)
            else MAX_IMAGES_PER_GAME
        )

        relevant_changed = (
            not self._settings_loaded
            or new_api_key != self._api_key
            or new_cache_limit_mb != self._cache_limit_mb
        )

        self._api_key = new_api_key
        self._cache_limit_mb = new_cache_limit_mb
        self._settings_loaded = True

        if new_api_key:
            # A key was just added (or was already present) -- if it's
            # later removed again, the nudge is fair game to fire once
            # more.
            self._warned_no_key = False

        if not relevant_changed:
            return

        profile = xbmcvfs.translatePath(addon.getAddonInfo("profile"))
        self._cache_dir = os.path.join(profile, CACHE_SUBDIR)
        xbmcvfs.mkdirs(self._cache_dir)
        with self._lock:
            self._index = self._load_manifest()
            # Covers the user lowering the limit mid-session -- without
            # this, shrinking the cap in Add-on Settings would only
            # take effect the next time something new gets fetched,
            # which might be a long time (or never) if the library's
            # already fully cached.
            self._enforce_cache_limit()

    def _manifest_path(self):
        return os.path.join(self._cache_dir, MANIFEST_NAME)

    def _load_manifest(self):
        try:
            with open(self._manifest_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}

        if data.get("_schema") != CACHE_SCHEMA_VERSION:
            log("GameArt: cache schema changed, starting fresh")
            return {}

        return data.get("games", {})

    def _save_manifest(self):
        # Caller already holds self._lock.
        try:
            with open(self._manifest_path(), "w", encoding="utf-8") as f:
                json.dump({"_schema": CACHE_SCHEMA_VERSION, "games": self._index}, f)
        except Exception as e:
            log(f"GameArt: could not save manifest: {e}")

    def _maybe_save_manifest(self):
        # Caller already holds self._lock. See MANIFEST_SAVE_INTERVAL.
        now = time.time()
        if now - self._last_manifest_save >= MANIFEST_SAVE_INTERVAL:
            self._save_manifest()
            self._last_manifest_save = now

    def _enforce_cache_limit(self):
        """Evict least-recently-used games' art until the cache is back
        under the configured size limit. Caller already holds self._lock.

        Eviction is per-game (all of a game's images at once), not per
        individual image file -- deleting only some of a game's hero
        images and leaving others would just leave a smaller, equally
        arbitrary rotation for it rather than actually freeing up
        meaningful space, and complicates "is this game still cached"
        logic for no real benefit.
        """
        limit_bytes = self._cache_limit_mb * 1024 * 1024
        if limit_bytes <= 0:
            return  # 0 = unlimited

        total = 0
        game_sizes = {}  # key -> total bytes for that game's images
        for key, entry in self._index.items():
            size = 0
            for name in entry.get("images") or []:
                try:
                    size += os.path.getsize(os.path.join(self._cache_dir, name))
                except OSError:
                    pass
            game_sizes[key] = size
            total += size

        if total <= limit_bytes:
            return

        # Oldest last_used_at first -- fetched_at as a fallback for
        # entries that predate this field (from before this feature),
        # so they're treated as "long unused" rather than crashing.
        by_recency = sorted(
            self._index.items(),
            key=lambda kv: kv[1].get("last_used_at", kv[1].get("fetched_at", 0)),
        )

        evicted = 0
        for key, entry in by_recency:
            if total <= limit_bytes:
                break
            size = game_sizes.get(key, 0)
            if size == 0 and (entry.get("images") or []):
                continue  # sizes couldn't be read; leave it rather than guess
            for name in entry.get("images") or []:
                try:
                    os.remove(os.path.join(self._cache_dir, name))
                except OSError:
                    pass
            total -= size
            del self._index[key]
            evicted += 1

        if evicted:
            log(
                f"GameArt: evicted {evicted} game(s) from the art cache "
                f"to stay under the {self._cache_limit_mb}MB limit"
            )
            self._save_manifest()
            self._last_manifest_save = time.time()

    def clear(self):
        """Delete every cached image and reset the manifest entirely --
        used by the "Clear art cache" button in Add-on Settings (see
        router.py's clear_art_cache action). Runs in whatever process
        Kodi invoked the settings action in, which is a separate,
        short-lived one from the long-running service.py -- so this
        doesn't coordinate with a fetch that service.py's own
        GameArtProvider instance might have in flight at the same
        moment; worst case, an in-progress fetch's images reappear in
        the manifest moments after a clear, which is harmless.
        """
        with self._lock:
            for entry in self._index.values():
                for name in entry.get("images") or []:
                    try:
                        os.remove(os.path.join(self._cache_dir, name))
                    except OSError:
                        pass
            self._index = {}
            self._save_manifest()
            self._last_manifest_save = time.time()

    def get(self, game_title, song_title=None):
        """Return whatever background image paths are already cached
        for this title (a list, possibly empty), and kick off a
        background fetch if we've never looked it up (or the last
        attempt failed long enough ago to be worth retrying).

        song_title is an optional fallback signal used only if a fetch
        actually happens and the album-title cascade comes up empty --
        see _resolve_game_id(). It has no effect on cache lookups
        (caching is keyed on game_title alone), so passing a different
        song_title for a later song of the same already-resolved (or
        already permanently-failed) album is harmless -- it's simply
        never consulted again once that album has an answer either way.
        """
        if not game_title:
            return []

        if not self._api_key:
            # Automatic mode with no key configured just fetches
            # nothing, silently, forever -- which looks identical to
            # "this is broken" from the outside. A single notification
            # the first time this is hit is enough to point at the fix
            # without nagging every time a new song plays.
            if not self._warned_no_key:
                self._warned_no_key = True
                xbmcgui.Dialog().notification(
                    "Rainwave",
                    "Add a SteamGridDB API key in Add-on Settings to enable automatic backgrounds",
                    xbmcgui.NOTIFICATION_INFO,
                    6000,
                )
            return []

        key = _cache_key(game_title)

        with self._lock:
            entry = self._index.get(key)
            is_pending = key in self._pending

            if entry is not None:
                images = entry.get("images") or []
                stale_failure = not images and (time.time() - entry.get("fetched_at", 0)) > FAILED_TTL
                if images or not stale_failure:
                    entry["last_used_at"] = time.time()
                    self._maybe_save_manifest()
                    return [os.path.join(self._cache_dir, name) for name in images]
                # else: fall through and re-fetch a stale failure

            if is_pending:
                return []

            if time.time() < self._rate_limited_until:
                # Still backing off from a 429 -- see
                # _handle_http_error(). Don't spawn a new attempt that
                # would just get rate-limited again; the next poll
                # after the backoff window passes will retry normally.
                return []

            self._pending.add(key)

        thread = threading.Thread(
            target=self._fetch, args=(game_title, song_title, key), daemon=True
        )
        thread.start()
        return []

    # -- background thread work below; never called from the main loop --

    def _fetch(self, game_title, song_title, key):
        try:
            images = self._fetch_images(game_title, song_title, key)
        except self._RateLimitedError:
            # Don't cache this as "no art found" -- we got rate-limited,
            # not a genuine empty result (see _RateLimitedError's
            # docstring). Just drop the pending flag so a later poll
            # retries for real, once get()'s backoff check lets a new
            # attempt through again.
            with self._lock:
                self._pending.discard(key)
            return
        except Exception as e:
            log(f"GameArt: lookup failed for '{game_title}': {e}")
            images = []

        with self._lock:
            now = time.time()
            self._index[key] = {
                "title": game_title,
                "images": images,
                "fetched_at": now,
                "last_used_at": now,
            }
            self._pending.discard(key)
            self._enforce_cache_limit()
            self._save_manifest()
            self._last_manifest_save = time.time()

    def _api_get(self, path, params=None):
        url = f"{API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))

    class _AuthError(Exception):
        """Raised internally when SteamGridDB rejects the API key --
        lets _resolve_game_id() stop trying further variants
        immediately instead of burning through the whole cascade with
        the same request doomed to fail every time.
        """

    class _RateLimitedError(Exception):
        """Raised internally on a 429 -- lets callers stop immediately
        (further variants would just get rate-limited too) and, more
        importantly, tells _fetch() not to cache this as a normal "no
        match" failure: a rate limit says nothing about whether the
        game actually has art on SteamGridDB, so treating it like a
        real negative result would wrongly lock that title out of
        retries for FAILED_TTL over something that had nothing to do
        with the title itself.
        """

    def _handle_http_error(self, e, context):
        """Given an HTTPError from any SteamGridDB call, either raise
        the appropriate internal signal for the caller to propagate
        (auth failure, rate limit) or just log it as an ordinary
        per-request miss that the caller can treat as "no result,
        try the next variant".
        """
        if e.code == 401:
            log("GameArt: SteamGridDB rejected the API key (401) -- check Add-on Settings")
            raise self._AuthError()
        if e.code == 429:
            retry_after = _parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
            with self._lock:
                self._rate_limited_until = time.time() + retry_after
            log(f"GameArt: SteamGridDB rate-limited (429) -- backing off {retry_after}s")
            raise self._RateLimitedError()
        log(f"GameArt: request failed for '{context}': HTTP {e.code}")

    def _search_once(self, variant):
        """Run a single autocomplete search and return (game_id,
        matched_name), or (None, None) if this particular query had no
        results. Raises _AuthError on a 401 or _RateLimitedError on a
        429, both of which callers let propagate rather than catching
        per-variant.
        """
        quoted = urllib.parse.quote(variant, safe="")
        try:
            search = self._api_get(f"/search/autocomplete/{quoted}")
        except urllib.error.HTTPError as e:
            self._handle_http_error(e, variant)
            return None, None
        except Exception as e:
            log(f"GameArt: search request failed for '{variant}': {e}")
            return None, None

        candidates = search.get("data") or []
        if not candidates:
            return None, None

        best = _best_candidate(candidates, variant)
        return best.get("id"), best.get("name", variant)

    def _first_match(self, variants):
        """Try each query in `variants`, in order, and return
        (game_id, matched_name, variant) for the first one that finds
        anything -- or None if none of them do.
        """
        for variant in variants:
            game_id, matched_name = self._search_once(variant)
            if game_id:
                return game_id, matched_name, variant
        return None

    def _resolve_game_id(self, game_title, song_title):
        """Find a game_id via both the album-title cascade (see
        _title_variants()) and the song-title cascade (see
        _song_title_variants()), and pick between them if both
        actually find something.

        Earlier versions of this tried the album title exhaustively
        and only even looked at the song title if the album cascade
        found *nothing at all* -- which meant a technically-valid but
        too-generic album match (e.g. a compilation album "Donkey Kong
        & Friends" truncating down to just "Donkey Kong") would always
        win over a much more specific song-title match (e.g. a song
        called "Donkey Kong Country Aquatic Ambience Revisited"
        resolving to "Donkey Kong Country") purely because it was
        tried first, never even attempting the song title once the
        album cascade already had *an* answer.

        Now both are tried every time, and if both succeed, the one
        whose winning query was more specific wins -- more words, or
        failing that more characters. The intuition: a search that
        needed less trimming/guessing to land on something is a
        stronger, less coincidental signal than one that only matched
        after being ground down to something short and generic. Ties
        (including "only one of them found anything") default to the
        album title, preserving its priority from before.

        This does mean a full song-title attempt now happens even when
        the album title alone would have been enough -- roughly
        doubling the worst-case API calls for a single lookup. Given
        how aggressively this is cached afterwards (a resolved game is
        never looked up again), that's a one-time cost per distinct
        game, not a recurring one, and was judged worth it for the
        accuracy gain. Returns (game_id, matched_name, source) where
        source is "album" or "song", or (None, None, None).
        """
        album_match = self._first_match(_title_variants(game_title))
        song_match = self._first_match(_song_title_variants(song_title)) if song_title else None

        if not album_match and not song_match:
            return None, None, None

        if album_match and not song_match:
            game_id, matched_name, variant = album_match
            if variant != game_title:
                log(
                    f"GameArt: '{game_title}' had no exact match, "
                    f"fell back to '{matched_name}' via query '{variant}'"
                )
            return game_id, matched_name, "album"

        if song_match and not album_match:
            game_id, matched_name, variant = song_match
            log(
                f"GameArt: '{game_title}' had no album-based match, "
                f"fell back to song-title hint '{matched_name}' via query '{variant}'"
            )
            return game_id, matched_name, "song"

        # Both found something -- more specific (words, then chars) wins.
        def specificity(variant):
            return (len(variant.split()), len(variant))

        album_id, album_name, album_variant = album_match
        song_id, song_name, song_variant = song_match

        if specificity(song_variant) > specificity(album_variant):
            log(
                f"GameArt: '{game_title}' matched both album ('{album_name}' via "
                f"'{album_variant}') and song title ('{song_name}' via '{song_variant}') "
                f"-- song-title match is more specific, using it"
            )
            return song_id, song_name, "song"

        return album_id, album_name, "album"

    def _download_heroes(self, game_id, matched_name, key, images, limit):
        """Fetch up to `limit` more hero images for `game_id` and
        append their downloaded filenames onto `images` in place.
        Returns how many were actually added. Shared by the primary
        matched game and, when that alone doesn't yield enough images,
        each of its series siblings (see _fill_from_series_siblings())
        -- all writing into the same `images` list under the same
        cache `key`, so they end up pooled together as one game's
        worth of rotation material regardless of which SteamGridDB
        entry each individual image actually came from.

        HTTPErrors are handled the same way as everywhere else in this
        class (see _handle_http_error()) -- notably, _AuthError/
        _RateLimitedError are allowed to propagate rather than being
        caught here, since the caller (ultimately _fetch()) needs to
        see those to avoid caching a false "no match".
        """
        if limit is not None and limit <= 0:
            return 0

        try:
            heroes = self._api_get(
                f"/heroes/game/{game_id}",
                {"dimensions": HERO_DIMENSIONS, "types": "static"},
            )
        except urllib.error.HTTPError as e:
            self._handle_http_error(e, matched_name)
            return 0
        except Exception as e:
            log(f"GameArt: heroes request failed for '{matched_name}': {e}")
            return 0

        entries = (heroes.get("data") or [])[:limit]
        added = 0
        for entry in entries:
            image_url = entry.get("url")
            if not image_url:
                continue
            ext = os.path.splitext(urllib.parse.urlparse(image_url).path)[1] or ".jpg"
            filename = f"{key}-{len(images)}{ext}"
            if self._download(image_url, filename):
                images.append(filename)
                added += 1
        return added

    def _fill_from_series_siblings(self, primary_game_id, matched_name, key, images):
        """When the matched game alone doesn't have enough hero art
        (see MIN_IMAGES_BEFORE_SERIES_LOOKUP), look for other games in
        the same numbered/subtitled series and borrow a couple of
        images from each -- e.g. "Last Bible III" turning up only one
        image is a good occasion to also try "Last Bible", "Last
        Bible II", etc., in case SteamGridDB's own search for the bare
        series name (see _series_base_name()) turns any of them up.

        A no-op if the matched name doesn't look like part of a
        numbered/subtitled series at all, or if the series-name search
        itself comes up empty -- this is a bonus on top of an already-
        successful match, not something worth failing over.
        """
        base_name = _series_base_name(matched_name)
        if not base_name:
            return

        quoted = urllib.parse.quote(base_name, safe="")
        try:
            search = self._api_get(f"/search/autocomplete/{quoted}")
        except urllib.error.HTTPError as e:
            self._handle_http_error(e, base_name)
            return
        except Exception as e:
            log(f"GameArt: series search failed for '{base_name}': {e}")
            return

        candidates = search.get("data") or []
        siblings = [
            c for c in candidates
            if c.get("id") != primary_game_id
            and c.get("name", "").lower().startswith(base_name.lower())
        ][:MAX_SERIES_SIBLINGS]

        if not siblings:
            return

        log(
            f"GameArt: '{matched_name}' only had {len(images)} image(s), "
            f"trying {len(siblings)} series sibling(s) of '{base_name}'"
        )

        for sibling in siblings:
            if self._max_images_per_game is not None and len(images) >= self._max_images_per_game:
                break
            sibling_id = sibling.get("id")
            if not sibling_id:
                continue
            remaining = MAX_IMAGES_PER_SIBLING
            if self._max_images_per_game is not None:
                remaining = min(remaining, self._max_images_per_game - len(images))
            self._download_heroes(sibling_id, sibling.get("name", base_name), key, images, remaining)

    def _fetch_images(self, game_title, song_title, key):
        try:
            game_id, matched_name, _source = self._resolve_game_id(game_title, song_title)
        except self._AuthError:
            return []
        # _RateLimitedError deliberately NOT caught here -- it needs
        # to propagate up to _fetch(), which handles it by skipping
        # the cache write entirely rather than recording a false "no
        # match" (see _RateLimitedError's docstring).

        if not game_id:
            tried = len(_title_variants(game_title)) + len(_song_title_variants(song_title))
            xbmc.log(
                f"[Rainwave] GameArt: no SteamGridDB match for '{game_title}' "
                f"(tried {tried} query variant(s), including song-title fallback)",
                xbmc.LOGDEBUG,
            )
            return []

        images = []
        self._download_heroes(game_id, matched_name, key, images, self._max_images_per_game)

        if len(images) < MIN_IMAGES_BEFORE_SERIES_LOOKUP:
            self._fill_from_series_siblings(game_id, matched_name, key, images)

        return images

    def _download(self, url, filename):
        dest = os.path.join(self._cache_dir, filename)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                data = r.read()
        except Exception as e:
            log(f"GameArt: could not download {url}: {e}")
            return False

        try:
            with open(dest, "wb") as f:
                f.write(data)
        except Exception as e:
            log(f"GameArt: could not write {dest}: {e}")
            return False

        return True
