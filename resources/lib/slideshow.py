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

    Two independent sources of images (chosen in Add-on Settings):

    - SOURCE_LOCAL: the original behaviour -- shuffle through every
      picture in a user-configured folder, unrelated to what's
      playing.
    - SOURCE_AUTO: pull background art for whatever game is currently
      playing from GameArtProvider (game_art.py), which fetches and
      caches it from SteamGridDB behind the scenes. Driven by
      set_current_game(), called from service.py with the same
      sync-delayed song data everything else uses, so backgrounds
      change in step with the audio rather than jumping ahead of it.
      The same folder setting used by SOURCE_LOCAL doubles as a
      fallback pool here: GameArtProvider can't find art for every
      title (an obscure remix album, an API outage, a still-in-flight
      fetch), and showing generic pictures beats a black screen for
      however long that lasts -- it's swapped back out automatically
      the moment real art becomes available for the current game.

    Both sources ultimately just populate self.files (a list of full
    image paths) for the same crossfade/rotation logic in tick() to
    consume -- the two modes only differ in *how* self.files gets
    populated.
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
        self._path = None
        self.active_slot = "A"
        self.next_change = 0
        self.preloaded = False
        self._current_game = None
        self._current_song_title = None
        self._settings_loaded = False
        self.reload_settings()

    def reload_settings(self):
        addon = xbmcaddon.Addon()
        new_enabled = addon.getSettingBool("slideshow_enabled")
        new_source = addon.getSettingInt("slideshow_source")
        new_path = addon.getSettingString("slideshow_path")
        self.interval = max(2, addon.getSettingInt("slideshow_time"))

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
        )

        self.enabled = new_enabled
        self.source = new_source
        self._path = new_path
        self._settings_loaded = True

        if not relevant_changed:
            return

        active = self.enabled and (self.source == SOURCE_AUTO or new_path)
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
        if self.enabled and new_path:
            self._scan_local(new_path)
            self.fallback_files = list(self.files)
        if not (self.enabled and self.source == SOURCE_LOCAL):
            self.files = []
        # SOURCE_AUTO's real (non-fallback) self.files is populated
        # lazily from tick()/set_current_game() below -- there's no
        # single folder to scan up front for it, and the current game
        # may not even be known yet.

        self.index = -1
        self.active_slot = "A"
        self.preloaded = False
        self.next_change = 0
        self._current_game = None
        self._current_song_title = None
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

    def set_current_game(self, game_title, song_title=None):
        """Auto mode only: called with whatever game (and, per the
        sync queue, audibly playing song) is currently on. A no-op
        unless the game is actually a change, so it's cheap to call on
        every delayed song application without worrying about
        redundant lookups -- GameArtProvider.get() is itself
        cheap/non-blocking too, but there's no reason to even call it
        for an unchanged title.

        song_title is only actually used the first time this game
        needs a fresh lookup (see GameArtProvider.get()/
        _resolve_game_id()) -- it's fine that it doesn't get updated
        again for later songs of the same still-unresolved album; see
        get()'s docstring for why that's harmless.
        """
        if self.source != SOURCE_AUTO or game_title == self._current_game:
            return
        self._current_game = game_title
        self._current_song_title = song_title
        # Deliberately not clearing self.files here: keep showing the
        # previous game's art (nothing on screen changes until tick()
        # below finds new files ready) rather than blanking out for
        # however long the lookup/fetch takes.

    def tick(self, now):
        """Call regularly (e.g. every second) from the service loop."""
        if not self.enabled:
            return

        if self.source == SOURCE_AUTO:
            if self.game_art and self._current_game:
                images = self.game_art.get(self._current_game, self._current_song_title)
            else:
                images = []

            if images:
                # A tuple of the actual image list, so two different
                # games (or the same game re-fetched) are correctly
                # seen as distinct, but re-polling the same unchanged
                # list isn't.
                key = ("game", tuple(images))
            elif self.fallback_files:
                # Deliberately *not* keyed on which game/title we fell
                # back for -- see the comment below on why staying
                # "fallback" across an unmatched-to-unmatched game
                # change doesn't retrigger a reshuffle.
                key = ("fallback",)
            else:
                key = None

            if key is not None and key != self._files_key:
                self._files_key = key
                if key[0] == "game":
                    self.files = images
                else:
                    # Shuffle a fresh copy on every real transition
                    # into fallback, rather than reusing whatever
                    # order _scan_local() shuffled once at startup --
                    # otherwise every fallback episode restarts at the
                    # same spot in the same fixed order, which is
                    # exactly the "always the same pictures first"
                    # problem this is fixing. Deliberately only on a
                    # genuine transition (game match found, then lost
                    # again -- or true startup) rather than every
                    # tick spent showing the fallback, or every
                    # unmatched-game-to-unmatched-game change within
                    # it: reshuffling constantly would restart the
                    # crossfade cycle non-stop instead of settling
                    # into a normal rotation.
                    self.files = list(self.fallback_files)
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
        self.index += 1
        if self.index >= len(self.files):
            self.index = 0
            random.shuffle(self.files)
        return self.files[self.index]
