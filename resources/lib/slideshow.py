import random

import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import log

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp")

# How long before a swap we start loading the next image into the
# hidden slot. Kodi still processes/loads textures for controls with
# <visible>false</visible>, so referencing the file here gets it
# decoded and cached ahead of time -- that's what avoids the flash.
PRELOAD_LEAD = 1.0  # seconds

SOURCE_LOCAL = 0
SOURCE_AUTO = 1
SOURCE_RANDOM = 2

# How often "Random (SteamGridDB)" mode pulls a fresh random batch
# from the cache, and how many images per batch -- see
# GameArtProvider.get_random_images(). Individual images within a
# batch still rotate at the usual per-interval cadence; this is a
# slower, separate "mix in some different games" cycle on top of that.
RANDOM_REFRESH_INTERVAL = 120  # seconds
RANDOM_IMAGE_COUNT = 8

# Index -> internal fallback mode for the "Automatic mode fallback"
# setting (values="Local folder|Random (SteamGridDB)|None").
AUTO_FALLBACK_OPTIONS = ("local", "random", "none")


class Slideshow:
    """Crossfades a background picture via Window(10000) properties.

    Kodi's <multiimage> control only accepts a literal integer for
    <timeperimage> -- it can't be driven by a setting. A single
    <image> control with <texture fadetime="..."> can be driven by a
    setting, but swapping one texture in place means Kodi has to
    decode the new file from disk *during* the transition, which
    shows a brief gap of whatever's behind the dialog.

    This class instead double-buffers between two slots (A/B): the
    next image is written into whichever slot is currently hidden
    a moment before the swap, so it's already decoded and cached by
    the time we flip Rainwave.SlideshowActive. The skin then crossfades
    the two controls via a VisibleChange animation.

    Three independent sources of images (chosen in Add-on Settings):

    - SOURCE_LOCAL: the original behaviour -- shuffle through every
      picture in a user-configured folder, unrelated to what's
      playing.
    - SOURCE_AUTO: pull background art for whatever game is currently
      playing from GameArtProvider (game_art.py), which fetches and
      caches it from SteamGridDB behind the scenes. Driven by
      set_current_game(), called from service.py with the same
      sync-delayed song data everything else uses, so backgrounds
      change in step with the audio rather than jumping ahead of it.
      Its fallback (used whenever GameArtProvider can't find art for
      the current title -- an obscure remix album, an API outage, a
      still-in-flight fetch) is configurable: the same local folder
      SOURCE_LOCAL uses, a random sampling of whatever's already been
      fetched from SteamGridDB (see SOURCE_RANDOM below), or nothing
      at all. Showing generic pictures beats a black screen for
      however long the gap lasts either way -- it's swapped back out
      automatically the moment real art becomes available for the
      current game.
    - SOURCE_RANDOM: cycles through a random sampling of whatever
      game art has already been fetched via SOURCE_AUTO (this session
      or a past one -- the cache persists across restarts), entirely
      unrelated to what's currently playing. See
      GameArtProvider.get_random_images().

    All three ultimately just populate self.files (a list of full
    image paths) for the same crossfade/rotation logic in tick() to
    consume -- they only differ in *how* self.files gets populated.
    """

    PATH_PROP = "Rainwave.SlideshowPath"
    ACTIVE_PROP = "Rainwave.SlideshowActive"
    IMAGE_PROPS = {"A": "Rainwave.SlideshowImageA", "B": "Rainwave.SlideshowImageB"}

    def __init__(self, game_art=None):
        self.home = xbmcgui.Window(10000)
        self.game_art = game_art
        self.files = []
        self.fallback_files = []
        self._files_key = None
        self.index = -1
        self.interval = 8
        self.enabled = False
        self.source = SOURCE_LOCAL
        self._auto_fallback = "local"
        self._path = None
        self.active_slot = "A"
        self.next_change = 0
        self.preloaded = False
        self._current_game = None
        self._current_song_title = None
        self._current_sid = None
        self._last_random_refresh = 0
        self._settings_loaded = False
        self.reload_settings()

    def reload_settings(self):
        addon = xbmcaddon.Addon()
        new_enabled = addon.getSettingBool("slideshow_enabled")
        new_source = addon.getSettingInt("slideshow_source")
        new_path = addon.getSettingString("slideshow_path")
        self.interval = max(2, addon.getSettingInt("slideshow_time"))

        fallback_index = addon.getSettingInt("auto_fallback_source")
        new_auto_fallback = (
            AUTO_FALLBACK_OPTIONS[fallback_index]
            if 0 <= fallback_index < len(AUTO_FALLBACK_OPTIONS)
            else "local"
        )

        if self.game_art:
            self.game_art.reload_settings()

        # reload_settings() runs on *every* addon settings change --
        # including ones with nothing to do with the slideshow, like
        # the stream sync offset -- so this only resets in-progress
        # state (current game, fetched art, rotation position) when
        # something that actually affects the slideshow changed.
        # Without this guard, changing an unrelated setting mid-song
        # would wipe the currently-displayed game art and fall back
        # to the local folder (or a blank screen) until the next real
        # song change happened to call set_current_game() again and
        # re-establish it.
        relevant_changed = (
            not self._settings_loaded
            or new_enabled != self.enabled
            or new_source != self.source
            or new_path != self._path
            or new_auto_fallback != self._auto_fallback
        )

        self.enabled = new_enabled
        self.source = new_source
        self._path = new_path
        self._auto_fallback = new_auto_fallback
        self._settings_loaded = True

        if not relevant_changed:
            return

        active = self.enabled and (self.source in (SOURCE_AUTO, SOURCE_RANDOM) or new_path)
        if active:
            # Only used by the skin as a "something to show" flag (see
            # script-rainwave-nowplaying.xml) -- any non-empty value
            # works, the actual per-source lookup happens below.
            self.home.setProperty(self.PATH_PROP, new_path if self.source == SOURCE_LOCAL else "auto")
        else:
            self.home.clearProperty(self.PATH_PROP)
            self.home.clearProperty(self.ACTIVE_PROP)
            for prop in self.IMAGE_PROPS.values():
                self.home.clearProperty(prop)

        self.files = []
        self.fallback_files = []
        # Only worth scanning the local folder if it's actually going
        # to be used for something: as the primary source (LOCAL), or
        # as Automatic mode's configured fallback.
        wants_local_scan = self.enabled and new_path and (
            self.source == SOURCE_LOCAL
            or (self.source == SOURCE_AUTO and self._auto_fallback == "local")
        )
        if wants_local_scan:
            self._scan_local(new_path)
            self.fallback_files = list(self.files)
        if not (self.enabled and self.source == SOURCE_LOCAL):
            self.files = []
        # SOURCE_AUTO's real (non-fallback) self.files is populated
        # lazily from tick()/set_current_game() below -- there's no
        # single folder to scan up front for it, and the current game
        # may not even be known yet. SOURCE_RANDOM is populated
        # lazily too, on its own refresh timer -- see tick().

        self._last_random_refresh = 0
        self.index = -1
        self.active_slot = "A"
        self.preloaded = False
        self.next_change = 0
        self._current_game = None
        self._current_song_title = None
        self._current_sid = None
        self._files_key = None

    def _scan_local(self, path):
        try:
            _dirs, files = xbmcvfs.listdir(path)
        except Exception:
            files = []
            log(f"Slideshow: could not list {path}")

        names = [f for f in files if f.lower().endswith(IMAGE_EXTS)]
        random.shuffle(names)

        sep = "" if path.endswith(("/", "\\")) else "/"
        self.files = [f"{path}{sep}{f}" for f in names]

        if not self.files:
            log(f"Slideshow: no images found in {path}")

    def set_current_game(self, game_title, song_title=None, sid=None):
        """Auto mode only: called with whatever game (and, per the
        sync queue, audibly playing song) is currently on. A no-op
        unless the game is actually a change, so it's cheap to call on
        every delayed song application without worrying about
        redundant lookups -- GameArtProvider.get() is itself
        cheap/non-blocking too, but there's no reason to even call it
        for an unchanged title.

        song_title and sid are only actually used the first time this
        game needs a fresh lookup (see GameArtProvider.get()/
        _resolve_game_id()) -- it's fine that they don't get updated
        again for later songs of the same still-unresolved album; see
        get()'s docstring for why that's harmless.
        """
        if self.source != SOURCE_AUTO or game_title == self._current_game:
            return
        self._current_game = game_title
        self._current_song_title = song_title
        self._current_sid = sid
        # Deliberately not clearing self.files here: keep showing the
        # previous game's art (nothing on screen changes until tick()
        # below finds new files ready) rather than blanking out for
        # however long the lookup/fetch takes.

    def _fallback_pool(self):
        """Whatever Automatic mode should show in place of real game
        art, per the "Automatic mode fallback" setting -- or an empty
        list if there's nothing usable (no folder configured for
        "local", nothing cached yet for "random", or the setting is
        "none"). Called fresh every time a fallback is actually
        needed (see tick()), not cached, so "random" pulls a new batch
        each fallback episode instead of reusing whatever it first
        happened to get.
        """
        if self._auto_fallback == "local":
            return self.fallback_files
        if self._auto_fallback == "random" and self.game_art:
            return self.game_art.get_random_images(count=RANDOM_IMAGE_COUNT)
        return []

    def tick(self, now):
        """Call regularly (e.g. every second) from the service loop."""
        if not self.enabled:
            return

        if self.source == SOURCE_AUTO:
            if self.game_art and self._current_game:
                images = self.game_art.get(self._current_game, self._current_song_title, self._current_sid)
            else:
                images = []

            if images:
                # A tuple of the actual image list, so two different
                # games (or the same game re-fetched) are correctly
                # seen as distinct, but re-polling the same unchanged
                # list isn't.
                key = ("game", tuple(images))
                fallback = None
            else:
                # Computed fresh each time a fallback is actually
                # needed, not cached -- "Random (SteamGridDB)" as the
                # fallback choice should pull a new batch each episode
                # rather than reusing whatever it first happened to
                # get (see _fallback_pool()).
                fallback = self._fallback_pool()
                # Deliberately *not* keyed on which game/title we fell
                # back for -- see the comment below on why staying
                # "fallback" across an unmatched-to-unmatched game
                # change doesn't retrigger a reshuffle.
                key = ("fallback",) if fallback else None

            if key is not None and key != self._files_key:
                self._files_key = key
                if key[0] == "game":
                    self.files = images
                else:
                    # Shuffle a fresh copy on every real transition
                    # into fallback, rather than reusing a fixed order
                    # -- otherwise every fallback episode would restart
                    # at the same spot, which is exactly the "always
                    # the same pictures first" problem this avoids.
                    # Deliberately only on a genuine transition (game
                    # match found, then lost again -- or true startup)
                    # rather than every tick spent showing the
                    # fallback, or every unmatched-game-to-unmatched-
                    # game change within it: reshuffling constantly
                    # would restart the crossfade cycle non-stop
                    # instead of settling into a normal rotation.
                    self.files = list(fallback)
                    random.shuffle(self.files)
                self.index = -1

                if self.next_change != 0:
                    # Something's already on screen (this isn't the
                    # very first image of the session) -- cross-fade
                    # into the new source's first image right away,
                    # via the same double-buffered swap normal
                    # rotation uses (see _crossfade_now()), rather
                    # than setting next_change = 0 here, which used to
                    # force every source switch through the "nothing
                    # on screen yet" branch below -- that branch
                    # writes straight into the visible slot with no
                    # previous image to fade from, which is exactly
                    # right for true startup, but produced a hard
                    # snap instead of a fade for a switch between
                    # game art and the fallback pool mid-session.
                    self._crossfade_now(now)
                # else: next_change is already 0, meaning this genuinely
                # is the first image of the session -- let the
                # "nothing on screen yet" branch below handle it.

        elif self.source == SOURCE_RANDOM:
            due_for_refresh = now - self._last_random_refresh >= RANDOM_REFRESH_INTERVAL
            if self.game_art and (due_for_refresh or not self.files):
                self._last_random_refresh = now
                images = self.game_art.get_random_images(count=RANDOM_IMAGE_COUNT)
                if images:
                    key = ("random", tuple(images))
                    if key != self._files_key:
                        self._files_key = key
                        self.files = images
                        self.index = -1
                        if self.next_change != 0:
                            self._crossfade_now(now)
                        # else: true startup -- let the "nothing on
                        # screen yet" branch below handle it, same as
                        # SOURCE_AUTO above.

        if not self.files:
            return

        if self.next_change == 0:
            # first image: nothing on screen yet, so just show it directly
            self.home.setProperty(self.IMAGE_PROPS["A"], self._next_file())
            self.home.setProperty(self.ACTIVE_PROP, "A")
            self.active_slot = "A"
            self.preloaded = False
            self.next_change = now + self.interval
            return

        if len(self.files) <= 1:
            # Nothing to rotate to -- most often a game with only one
            # hero image on SteamGridDB (common for less well-known
            # titles). Without this, the periodic swap logic below
            # would still fire every `interval` seconds, flipping the
            # active slot between two copies of the *same* picture:
            # visually a no-op in principle, but Kodi still treats
            # that as a fresh texture load each time, which can show
            # up as a brief flicker for no actual change. So it just
            # stays on screen, untouched, for as long as it's the
            # only image available (i.e. for the rest of the song, or
            # until a source/game change brings in something new via
            # the transition handling above).
            return

        lead = min(PRELOAD_LEAD, self.interval / 2)
        time_left = self.next_change - now

        if not self.preloaded and time_left <= lead:
            hidden_slot = "B" if self.active_slot == "A" else "A"
            self.home.setProperty(self.IMAGE_PROPS[hidden_slot], self._next_file())
            self.preloaded = True

        if now >= self.next_change:
            self.active_slot = "B" if self.active_slot == "A" else "A"
            self.home.setProperty(self.ACTIVE_PROP, self.active_slot)
            self.next_change = now + self.interval
            self.preloaded = False

    def _crossfade_now(self, now):
        """Cross-fade to the next file right away, using the exact
        same double-buffered slot mechanism as a normal scheduled
        swap below -- just without waiting for the interval or the
        preload lead to elapse first. Used when the image *source*
        changes (game art found/lost/switched) so that transition
        fades the same way every other image change does, instead of
        the "nothing on screen yet" bootstrap path snapping straight
        to it.
        """
        hidden_slot = "B" if self.active_slot == "A" else "A"
        self.home.setProperty(self.IMAGE_PROPS[hidden_slot], self._next_file())
        self.active_slot = hidden_slot
        self.home.setProperty(self.ACTIVE_PROP, self.active_slot)
        self.next_change = now + self.interval
        self.preloaded = False

    def _next_file(self):
        if self.index + 1 >= len(self.files):
            # Wrapping around: reshuffle for the next lap, but guard
            # against the shuffle happening to put whichever picture
            # is still on screen right back at the front -- that would
            # show the same image twice in a row across the wrap,
            # which random.shuffle() alone doesn't prevent.
            previous = self.files[self.index] if 0 <= self.index < len(self.files) else None
            random.shuffle(self.files)
            if previous is not None and len(self.files) > 1 and self.files[0] == previous:
                swap_with = random.randint(1, len(self.files) - 1)
                self.files[0], self.files[swap_with] = self.files[swap_with], self.files[0]
            self.index = 0
        else:
            self.index += 1
        return self.files[self.index]
