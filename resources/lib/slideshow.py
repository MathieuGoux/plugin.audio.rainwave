"""
Rainwave Kodi Addon - Background Slideshow

This module provides the Slideshow class which manages the background image slideshow with multiple image sources and smooth transitions.

OVERVIEW:
========

The slideshow displays background images behind the now-playing widget. It supports three different image sources and uses a double-buffering technique for smooth cross-fade transitions between images.

KEY FEATURES:
    - Three image sources: Local folder, Automatic (SteamGridDB), Random
    - Double-buffering for smooth cross-fade transitions
    - Preloading to prevent visual gaps during transitions
    - Configurable rotation interval
    - Configurable fallback behavior for Automatic mode
    - Settings changes take effect immediately without restart
    - Manual game selection override

DOUBLE-BUFFERING PATTERN:
========================

Kodi's <multiimage> control only accepts a literal integer for <timeperimage>, so it can't be driven by a setting. A single <image> control with <texture> can be driven by a setting, but swapping one texture in place means Kodi must decode the new file from disk *during* the transition, which shows a brief gap of whatever's behind the dialog.

THIS CLASS USES DOUBLE-BUFFERING:
    1. Maintains two image slots: A and B
    2. Next image is written to the HIDDEN slot before the swap
    3. It's already decoded and cached by the time we flip the active slot
    4. The skin crossfades the two controls via VisibleChange animation
    5. No visual gaps, smooth transitions

This is the same pattern used in widget.py for next candidate rotation.

IMAGE SOURCES:
=============

1. SOURCE_LOCAL (0):
   - Cycles through images in a user-configured local folder
   - Unrelated to what's playing
   - Simple, no external dependencies

2. SOURCE_AUTO (1):
   - Fetches artwork for the currently playing game from SteamGridDB
   - Uses GameArtProvider for fetching and caching
   - Changes in sync with the audio (via SyncQueue)
   - Has configurable fallback when no artwork found

3. SOURCE_RANDOM (2):
   - Randomly selects from cached game artwork
   - Uses images previously fetched via SOURCE_AUTO
   - Unrelated to what's currently playing

ALL THREE SOURCES populate self.files (list of image paths) for the same crossfade/rotation logic in tick() to consume. They only differ in *how* self.files gets populated.
"""

import random
import time
import xbmcaddon
import xbmcgui
import xbmcvfs

from .utils import log

#==CONSTANTS================

# Supported image file extensions for local folder scanning
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".bmp")

# How long before a swap we start loading the next image into the hidden slot. Kodi still processes/loads textures for controls with <visible>false</visible>, so referencing the file here gets it decoded and cached ahead of time -- that's what avoids the flash.
PRELOAD_LEAD = 1.0  # seconds

#Image source mode constants
SOURCE_LOCAL = 0 # Local folder mode
SOURCE_AUTO = 1 # Automatic (SteamGriDB) mode
SOURCE_RANDOM = 2 # Random cached art mode

# Random mode configuration
# How often "Random (SteamGridDB)" mode pulls a fresh random batch from the cache, and how many images per batch (see GameArtProvider.get_random_images()). Individual images within a batch still rotate at the usual per-interval cadence; this is a slower, separate "mix in some different games" cycle on top of that.
RANDOM_REFRESH_INTERVAL = 120  # seconds
RANDOM_IMAGE_COUNT = 8

# Automatic mode fallback options
# Maps to the "Automatic mode fallback" setting in settings.xml
AUTO_FALLBACK_OPTIONS = ("local", "random", "none")


class Slideshow:
    """
    Crossfades a background picture via Window(10000) properties.

    Kodi's <multiimage> control only accepts a literal integer for <timeperimage> -- it can't be driven by a setting. A single <image> control with <texture fadetime="..."> can be driven by a setting, but swapping one texture in place means Kodi has to decode the new file from disk *during* the transition, which shows a brief gap of whatever's behind the dialog.

    This class instead double-buffers between two slots (A/B): the next image is written into whichever slot is currently hidden a moment before the swap, so it's already decoded and cached by the time we flip Rainwave.SlideshowActive. The skin then crossfades the two controls via a VisibleChange animation.

    Three independent sources of images (chosen in Add-on Settings):

        - SOURCE_LOCAL: the original behaviour: shuffle through every picture in a user-configured folder, unrelated to what's playing.
    
        - SOURCE_AUTO: pull background art for whatever game is currently playing from GameArtProvider (game_art.py), which fetches and caches it from SteamGridDB behind the scenes. Driven by set_current_game(), called from service.py with the same sync-delayed song data everything else uses, so backgrounds change in step with the audio rather than jumping ahead of it. Its fallback (used whenever GameArtProvider can't find art for the current title: an obscure remix album, an API outage, a still-in-flight fetch) is configurable: the same local folder SOURCE_LOCAL uses, a random sampling of whatever's already been fetched from SteamGridDB (see SOURCE_RANDOM below), or nothing at all. Showing generic pictures beats a black screen for however long the gap lasts either way. It's swapped back out automatically the moment real art becomes available for the current game.
        
        - SOURCE_RANDOM: cycles through a random sampling of whatever game art has already been fetched via SOURCE_AUTO (this session or a past one: the cache persists across restarts), entirely unrelated to what's currently playing. See GameArtProvider.get_random_images().

    All three ultimately just populate self.files (a list of full image paths) for the same crossfade/rotation logic in tick() to consume -- they only differ in *how* self.files gets populated.
    
    ATTRIBUTES:
        home: Window(10000) instance for property access
        game_art: GameArtProvider instance (for SOURCE_AUTO and SOURCE_RANDOM)
        files: List of current image paths to display
        fallback_files: List of fallback image paths (for SOURCE_AUTO)
        _files_key: Key for detecting when image source changes
        index: Current index in files list
        interval: Rotation interval in seconds (from settings)
        enabled: Whether slideshow is enabled (from settings)
        source: Current image source mode (SOURCE_LOCAL/AUTO/RANDOM)
        _auto_fallback: Fallback mode for SOURCE_AUTO
        _path: Local folder path (for SOURCE_LOCAL)
        active_slot: Currently visible slot ("A" or "B")
        next_change: Timestamp when next rotation should happen
        preloaded: Whether next image is preloaded
        _current_game: Current game for SOURCE_AUTO mode
        _current_song_title: Current song title for SOURCE_AUTO mode
        _current_sid: Current station ID for SOURCE_AUTO mode
        _last_random_refresh: When random batch was last refreshed
        _settings_loaded: Whether settings have been loaded
        _files_key: Key for detecting when to refresh display

    WINDOW PROPERTIES:
        PATH_PROP: Set to path or "auto" when active, cleared when inactive
        ACTIVE_PROP: Set to "A" or "B" to indicate which slot is visible
        IMAGE_PROPS["A"]: Image path for slot A
        IMAGE_PROPS["B"]: Image path for slot B

    LIFECYCLE:
        1. Created in service.py run() function
        2. reload_settings() called to initialize from settings
        3. tick() called every second from service loop
        4. set_current_game() called when game changes (SOURCE_AUTO only)
        5. set_manual_game() called when user selects game manually
    """
    
    # Window(10000) property names used for communication with skin XML
    PATH_PROP = "Rainwave.SlideshowPath" # Indicates slideshow is active
    ACTIVE_PROP = "Rainwave.SlideshowActive" # Current active slot: "A" or "B"
    IMAGE_PROPS = {
        "A": "Rainwave.SlideshowImageA", # Image path for slot A
        "B": "Rainwave.SlideshowImageB"  # Image path for slot B
    }

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
        """
        Reload all settings from addon configuration.

        This is called:
            - On initialization
            - Every time settings change (via SettingsMonitor in service.py)

        ONLY RESETS STATE WHEN RELEVANT SETTINGS CHANGE:
            Since reload_settings() runs on EVERY addon settings change (including ones unrelated to slideshow), we only reset in-progress state when settings that actually affect the slideshow changed.

            Without this guard, changing an unrelated setting (like stream sync offset) mid-song would wipe the currently-displayed game art and fall back to local folder until the next real song change.

        RELOAD PROCESS:
            1. Read all slideshow-related settings
            2. Check if any relevant setting changed
            3. If not, return early (no-op)
            4. If yes, reset all state and re-scan local folder if needed

        SETTINGS RELOADED:
            - slideshow_enabled: Master enable/disable
            - slideshow_source: Image source mode
            - slideshow_path: Local folder path
            - slideshow_time: Rotation interval
            - auto_fallback_source: Fallback mode for SOURCE_AUTO
            - steamgriddb_api_key: API key (passed to game_art if exists)
            - art_cache_limit: Cache limit (passed to game_art if exists)
        """
        
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
            '''
            Only used by the skin as a "something to show" flag (see script-rainwave-nowplaying.xml) -- any non-empty value works, the actual per-source lookup happens below.
            '''
            
            self.home.setProperty(self.PATH_PROP, new_path if self.source == SOURCE_LOCAL else "auto")
        else:
            self.home.clearProperty(self.PATH_PROP)
            self.home.clearProperty(self.ACTIVE_PROP)
            for prop in self.IMAGE_PROPS.values():
                self.home.clearProperty(prop)

        self.files = []
        self.fallback_files = []
        
        '''
        Only worth scanning the local folder if it's actually going to be used for something: as the primary source (LOCAL), or as Automatic mode's configured fallback.
        '''
        
        wants_local_scan = self.enabled and new_path and (
            self.source == SOURCE_LOCAL
            or (self.source == SOURCE_AUTO and self._auto_fallback == "local")
        )
        if wants_local_scan:
            self._scan_local(new_path)
            self.fallback_files = list(self.files)
        if not (self.enabled and self.source == SOURCE_LOCAL):
            self.files = []
        
        '''
        SOURCE_AUTO's real (non-fallback) self.files is populated lazily from tick()/set_current_game() below. There's no single folder to scan up front for it, and the current game may not even be known yet. SOURCE_RANDOM is populated lazily too, on its own refresch timer; see tick().
        '''
        
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
        """
        Scan a local folder for images.

        ARGS:
            path (str): Folder path to scan

        BEHAVIOR:
            1. List all files in the folder using xbmcvfs.listdir
            2. Filter to supported image extensions (IMAGE_EXTS)
            3. Shuffle the list randomly for variety
            4. Build full paths for each image
            5. Store in self.files

        ERROR HANDLING:
            - If folder doesn't exist or can't be read, logs error and sets self.files to empty list
            - If no images found, logs warning and sets self.files to empty list

        PATH HANDLING:
            Handles both trailing slash and non-trailing slash paths:
                - "path/to/folder" -> "path/to/folder/image.jpg"
                - "path/to/folder/" -> "path/to/folder/image.jpg"

        RANDOMIZATION:
            Uses random.shuffle() to randomize the order of images. This ensures variety each time the slideshow starts.
        """
        
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
        """
        Set the current game for Automatic mode.

        This is called by service.py with sync-delayed song data to ensure backgrounds change in step with the audio rather than jumping ahead.

        ARGS:
            game_title (str): Album title (usually the game name)
            song_title (str, optional): Song title (fallback for search)
            sid (int, optional): Station ID (affects search logic)

        BEHAVIOR:
            - No-op if source is not SOURCE_AUTO or game hasn't changed
            - Updates internal state (_current_game, _current_song_title, _current_sid)
            - Does NOT clear self.files - keeps showing previous game's art until new art is ready (prevents black screen during lookup)
            - song_title and sid only used the first time this game needs a fresh lookup (see GameArtProvider.get() for details)

        NOTE ON SONG_TITLE AND SID:
            For most stations, the album title might not be the exact game name. The song title can help as a fallback search term. The station ID affects the search strategy (Game station uses album-only search).

        However, for subsequent songs from the same album, we don't need to re-pass song_title and sid because the game is already resolved.
        """
        
        if self.source != SOURCE_AUTO:
            return

        self._current_song_title = song_title
        self._current_sid = sid

        if game_title == self._current_game:
            return
        self._current_game = game_title
        
        '''
        Deliberately not clearing self.files here: keep showing the previous game's art (nothing on screen changes until tick() below finds new files ready) rather than blanking out for however long the lookup/fetch takes.
        '''
        
    def _fallback_pool(self):
        """
        Get the current fallback image pool for Automatic mode.

        Called when Automatic mode can't find artwork for the current game.

        RETURNS:
            list: List of image paths based on fallback setting:
                - "local": Images from configured local folder
                - "random": Random cached game artwork (RANDOM_IMAGE_COUNT images)
                - "none": Empty list (no fallback, results in black screen)

        CALLED FRESH EACH TIME:
            This method is called fresh every time a fallback is needed, not cached. This ensures:
                - "random" mode gets a new batch each time (not reusing old batch)
                - "local" mode always reflects current local folder contents

        FALLBACK BEHAVIOR:
            When Automatic mode can't find artwork:
                1. _fallback_pool() is called to get fallback images
                2. If fallback is empty, display goes black (or shows previous images)
                3. If fallback has images, they're displayed instead
                4. When real artwork becomes available, it replaces the fallback

            This ensures the user always sees something (anything) rather than a black screen.
        """
        
        if self._auto_fallback == "local":
            return self.fallback_files
        if self._auto_fallback == "random" and self.game_art:
            return self.game_art.get_random_images(count=RANDOM_IMAGE_COUNT)
        return []

    def tick(self, now):
        """
        Main update method - called every second from service loop.

        This is the heart of the slideshow functionality. It handles:
            1. Manual game selection (from Information key press)
            2. SOURCE_AUTO: Fetching and displaying game artwork
            3. SOURCE_RANDOM: Refreshing random image batch
            4. Image rotation with double-buffering
            5. Preloading next image
            6. Cross-fade transitions

        ARGS:
            now (float): Current timestamp from time.time()

        DESIGN:
            This method is designed to be called frequently (every second) with minimal overhead when no action is needed. Most of the complex logic is gated by conditional checks.

        FLOW:
            1. Check for manual game selection (from Window property)
            2. If enabled, handle based on current source:
                a. SOURCE_AUTO: Get game artwork, handle fallback
                b. SOURCE_RANDOM: Refresh random batch if due
            3. If no files, return (nothing to display)
            4. Handle first image display (no rotation yet)
            5. Handle single image case (no rotation needed)
            6. Preload next image if due
            7. Perform rotation if due

        MANUAL GAME SELECTION:
            Checks for "Rainwave.ManualGame" property set by game_selector.py. If found, clears the property and calls set_manual_game().

        MANUAL OVERRIDE REFRESH:
            Checks for "Rainwave.OverridesDirty" property set by game_selector.py's _save_manual_override() right after it writes a new/changed override to manifest.json on disk. self.game_art (the GameArtProvider actually used below) only reloads its own in-memory manual_overrides after completing a fetch or via a 5-minute throttle otherwise (see game_art.py's _save_manifest()/_maybe_save_manifest()), so without this, a just-saved override could sit invisible to this running instance -- still serving whatever was cached before it existed -- for up to 5 minutes.
        """
        
        manual_game = xbmcgui.Window(10000).getProperty("Rainwave.ManualGame")
        if manual_game:
            xbmcgui.Window(10000).clearProperty("Rainwave.ManualGame")
            self.set_manual_game(manual_game)

        overrides_dirty = xbmcgui.Window(10000).getProperty("Rainwave.OverridesDirty")
        if overrides_dirty:
            xbmcgui.Window(10000).clearProperty("Rainwave.OverridesDirty")
            if self.game_art:
                self.game_art.reload_manual_overrides()
    
        """
        Call regularly (e.g. every second) from the service loop.
        """
        
        if not self.enabled:
            return

        if self.source == SOURCE_AUTO:
            if self.game_art and self._current_game:
                images = self.game_art.get(self._current_game, self._current_song_title, self._current_sid)
            else:
                images = []

            if images:
                '''
                A tuple of the actual image list, so two different games (or the same game re-fetched) are correctly seen as distinct, but re-polling the same unchanged list isn't.
                '''
                
                key = ("game", tuple(images))
                fallback = None
            else:
                '''
                Computed fresh each time a fallback is actually needed, not cached -- "Random (SteamGridDB)" as the fallback choice should pull a new batch each episode rather than reusing whatever it first happened to get (see _fallback_pool()).
                '''
                
                fallback = self._fallback_pool()
                
                '''
                Deliberately *not* keyed on which game/title we fell back for: see the comment below on why staying "fallback" across an unmatched-to-unmatched game change doesn't retrigger a reshuffle.
                '''
                
                key = ("fallback",) if fallback else None

            if key is not None and key != self._files_key:
                self._files_key = key
                if key[0] == "game":
                    self.files = images
                else:
                    '''
                    Shuffle a fresh copy on every real transition into fallback, rather than reusing a fixed order; otherwise every fallback episode would restart at the same spot, which is exactly the "always the same pictures first" problem this avoids. Deliberately only on a genuine transition (game match found, then lost again, or true startup) rather than every tick spent showing the fallback, or every unmatched-game-to-unmatched-game change within it: reshuffling constantly would restart the crossfade cycle non-stop instead of settling into a normal rotation.
                    '''
                    
                    self.files = list(fallback)
                    random.shuffle(self.files)
                self.index = -1

                if self.next_change != 0:
                    '''
                    Something's already on screen (this isn't the very first image of the session): cross-fade into the new source's first image right away, via the same double-buffered swap normal rotation uses (see _crossfade_now()), rather than setting next_change = 0 here, which used to force every source switch through the "nothing on screen yet" branch below. That branch writes straight into the visible slot with no previous image to fade from, which is exactly right for true startup, but produced a hard snap instead of a fade for a switch between game art and the fallback pool mid-session.
                    '''
                    
                    self._crossfade_now(now)
                '''
                else: next_change is already 0, meaning this genuinely is the first image of the session. Let the "nothing on screen yet" branch below handle it.
                '''
                
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
                        '''
                        else: true startup. Let the "nothing on screen yet" branch below handle it, same as SOURCE_AUTO above.
                        '''
                        
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
            '''
            Nothing to rotate to: most often a game with only one hero image on SteamGridDB (common for less well-known titles). Without this, the periodic swap logic below would still fire every `interval` seconds, flipping the active slot between two copies of the *same* picture: visually a no-op in principle, but Kodi still treats that as a fresh texture load each time, which can show up as a brief flicker for no actual change. So it just stays on screen, untouched, for as long as it's the only image available (i.e. for the rest of the song, or until a source/game change brings in something new via the transition handling above).
            '''
            
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
        """
        Cross-fade to the next file immediately.

        Used when the image SOURCE changes (not just the image itself) to ensure the transition fades smoothly rather than snapping.

        EXAMPLES OF SOURCE CHANGES:
            - Game art found after showing fallback
            - Fallback triggered after game art was showing
            - Switching between game art for different games
            - Switching from fallback to game art

        USES SAME DOUBLE-BUFFERING AS NORMAL ROTATION:
            This method uses the exact same slot-swapping mechanism as the regular rotation in tick(). The difference is it happens immediately rather than waiting for the interval timer.

        ARGS:
            now (float): Current timestamp from time.time()
        """
        
        hidden_slot = "B" if self.active_slot == "A" else "A"
        self.home.setProperty(self.IMAGE_PROPS[hidden_slot], self._next_file())
        self.active_slot = hidden_slot
        self.home.setProperty(self.ACTIVE_PROP, self.active_slot)
        self.next_change = now + self.interval
        self.preloaded = False

    def _next_file(self):
        """
        Get the next image path in the rotation.

        Advances the index and wraps around to the beginning if at the end.

        RETURNS:
            str: Path of the next image to display

        BEHAVIOR:
            - Increments self.index
            - If index >= len(self.files), wraps to 0
            - Returns self.files[self.index]

        This is used by:
            - tick() for normal rotation
            - _crossfade_now() for immediate transitions
            - First image display
        """
        
        if self.index + 1 >= len(self.files):
            '''
            Wrapping around: reshuffle for the next lap, but guard against the shuffle happening to put whichever picture is still on screen right back at the front. That would show the same image twice in a row across the wrap, which random.shuffle() alone doesn't prevent.
            '''
            
            previous = self.files[self.index] if 0 <= self.index < len(self.files) else None
            random.shuffle(self.files)
            if previous is not None and len(self.files) > 1 and self.files[0] == previous:
                swap_with = random.randint(1, len(self.files) - 1)
                self.files[0], self.files[swap_with] = self.files[swap_with], self.files[0]
            self.index = 0
        else:
            self.index += 1
        return self.files[self.index]

    def set_manual_game(self, game_name: str):
        """Force slideshow to use art from a specific game."""
        self._manual_game = game_name
        self._manual_game_set_at = time.time()
        self._current_game = game_name
        self._current_game_sid = None
        self.tick(time.time())  # Trigger immediate update