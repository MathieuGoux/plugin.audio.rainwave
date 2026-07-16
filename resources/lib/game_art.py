import difflib
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon
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
CACHE_SCHEMA_VERSION = 3

# How long to remember "no match / no art found for this title" before
# letting a future lookup try again. Long enough that a title with no
# real match doesn't get hammered every time it comes up in rotation
# (Rainwave's library repeats constantly), short enough that a game
# added to SteamGridDB after our first attempt eventually gets found.
FAILED_TTL = 7 * 24 * 60 * 60  # 7 days

# How many hero images to keep per game, when SteamGridDB has several
# -- lets the slideshow rotate between a few pieces of art for a game
# that's airing several songs in a row, instead of one static image.
MAX_IMAGES_PER_GAME = 4

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
# match anything on SteamGridDB, because neither is a real game. Both
# examples share a shape though: [real game name] + [one modifier
# word tacked on the end]. _title_variants() exploits that shape --
# strip a known modifier word if the title ends with one, and failing
# that, progressively drop trailing words -- to build a short list of
# alternate search queries to fall back through when the exact title
# comes up empty. It's a heuristic, not a real fuzzy-search API (
# SteamGridDB's search itself is closer to substring matching than
# typo-tolerant fuzzy matching), so it won't catch everything, but it
# resolves the common "extra word(s) appended" case cheaply.
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

# Below this many words, further truncation stops being "drop a
# descriptor word" and starts being "guess at a completely different,
# much more generic game" -- e.g. truncating "Chrono Trigger" to
# "Chrono" risks matching some other, wrong game entirely.
MIN_TRUNCATED_WORDS = 2

# Total alternate queries to try (including the original and the
# suffix-stripped version) before giving up -- keeps a title with no
# real match from generating an unbounded number of API calls.
MAX_VARIANTS = 5


def _title_variants(title):
    """Build an ordered, deduplicated list of search queries to try
    for a game title: the title as-is, then with known trailing
    modifier words stripped, then progressively shorter truncations
    of that. See the comment above _STRIP_SUFFIXES for why.
    """
    variants = []

    def add(v):
        v = v.strip()
        if v and v not in variants:
            variants.append(v)

    add(title)

    cleaned = title
    while True:
        stripped = _STRIP_SUFFIXES.sub("", cleaned).strip()
        if not stripped or stripped == cleaned:
            break
        cleaned = stripped
    add(cleaned)

    words = cleaned.split()
    while len(words) > MIN_TRUNCATED_WORDS and len(variants) < MAX_VARIANTS:
        words = words[:-1]
        add(" ".join(words))

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
        self.reload_settings()

    def reload_settings(self):
        addon = xbmcaddon.Addon()
        self._api_key = addon.getSettingString("steamgriddb_api_key").strip()
        self._cache_limit_mb = addon.getSettingInt("art_cache_limit_mb")
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

    def get(self, game_title):
        """Return whatever background image paths are already cached
        for this title (a list, possibly empty), and kick off a
        background fetch if we've never looked it up (or the last
        attempt failed long enough ago to be worth retrying).
        """
        if not game_title or not self._api_key:
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

            self._pending.add(key)

        thread = threading.Thread(
            target=self._fetch, args=(game_title, key), daemon=True
        )
        thread.start()
        return []

    # -- background thread work below; never called from the main loop --

    def _fetch(self, game_title, key):
        try:
            images = self._fetch_images(game_title, key)
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

    def _fetch_images(self, game_title, key):
        game_id = None
        matched_name = None

        for variant in _title_variants(game_title):
            quoted = urllib.parse.quote(variant, safe="")
            try:
                search = self._api_get(f"/search/autocomplete/{quoted}")
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    log("GameArt: SteamGridDB rejected the API key (401) -- check Add-on Settings")
                    return []
                log(f"GameArt: search request failed for '{variant}': HTTP {e.code}")
                continue
            except Exception as e:
                log(f"GameArt: search request failed for '{variant}': {e}")
                continue

            candidates = search.get("data") or []
            if not candidates:
                continue

            best = _best_candidate(candidates, variant)
            game_id = best.get("id")
            matched_name = best.get("name", variant)
            if game_id:
                if variant != game_title:
                    log(
                        f"GameArt: '{game_title}' had no exact match, "
                        f"fell back to '{matched_name}' via query '{variant}'"
                    )
                break

        if not game_id:
            xbmc.log(
                f"[Rainwave] GameArt: no SteamGridDB match for '{game_title}' "
                f"(tried {len(_title_variants(game_title))} query variants)",
                xbmc.LOGDEBUG,
            )
            return []

        try:
            heroes = self._api_get(
                f"/heroes/game/{game_id}",
                {"dimensions": HERO_DIMENSIONS, "types": "static"},
            )
        except Exception as e:
            log(f"GameArt: heroes request failed for '{matched_name}': {e}")
            return []

        entries = (heroes.get("data") or [])[:MAX_IMAGES_PER_GAME]
        images = []
        for i, entry in enumerate(entries):
            image_url = entry.get("url")
            if not image_url:
                continue
            ext = os.path.splitext(urllib.parse.urlparse(image_url).path)[1] or ".jpg"
            filename = f"{key}-{i}{ext}"
            if self._download(image_url, filename):
                images.append(filename)

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
