import os
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib.api import RainwaveAPI
from resources.lib.widget import Widget
from resources.lib.nowplaying_dialog import NowPlayingDialog
from resources.lib.slideshow import Slideshow
from resources.lib.game_art import GameArtProvider
from resources.lib.sync_queue import SyncQueue
from resources.lib.utils import log

POLL_INTERVAL = 5  # seconds, Rainwave "now playing" refresh
# Main loop granularity. Used to be 1s, which was fine for everything
# else here (slideshow rotation, sync-queue polling) since all of that
# is gated by its own "has enough real time elapsed" checks rather
# than by how often the loop happens to run -- calling them 10x more
# often costs nothing, they just no-op in between. It's too coarse for
# the buffering spinner below though: cycling through 12 frames at 1
# per second would take 12 whole seconds for one rotation, unusably
# slow for something meant to read as continuous motion.
TICK = 0.1  # seconds
STREAM_HOST = "relay.rainwave.cc"

# Frame files: resources/media/spinner_00.png .. spinner_{N-1:02d}.png
SPINNER_FRAME_COUNT = 12
SPINNER_FRAME_INTERVAL = 0.1  # seconds per frame -> ~1.2s per full spin


class RainwavePlayerMonitor(xbmc.Player):
    """Shows/hides the widget based on whether Kodi is actually
    playing a Rainwave stream (as opposed to any other audio).

    service.py runs in its own long-lived process, separate from the
    plugin process that handles router.py/default.py -- Kodi starts a
    fresh interpreter for every plugin:// invocation. The two never
    share a Python object, so the only way they can talk to each
    other is through Window(10000) properties: router.py sets
    "Rainwave.CurrentStation" when a station is tuned in, and that's
    what we read here to know which sid to poll.
    """

    def __init__(self, widget, dialog, sync_queue, slideshow, spinner_dir):
        super().__init__()
        self.widget = widget
        self.dialog = dialog
        self.sync_queue = sync_queue
        self.slideshow = slideshow
        self.spinner_dir = spinner_dir
        self.active = False
        self.home = xbmcgui.Window(10000)
        self._not_playing_streak = 0
        self._last_song_key = None

    def _is_rainwave_stream(self):
        try:
            return STREAM_HOST in self.getPlayingFile()
        except Exception:
            return False

    def _current_sid(self):
        sid = self.home.getProperty("Rainwave.CurrentStation")
        return int(sid) if sid else None

    def _check_active_state(self):
        # A live internet radio stream can hiccup (a brief buffering
        # stall, a momentary reconnect) without playback actually
        # ending from the listener's point of view -- but Kodi's
        # engine can still fire onPlayBackStopped/onPlayBackError for
        # that split second. Previously we trusted those callbacks
        # unconditionally and deactivated on the spot; since audio
        # then kept playing on the same continuous connection,
        # onAVStarted never fired again to reactivate us, so polling
        # (and the widget) stayed dead for the rest of the session --
        # exactly matching "first song shows, then nothing updates
        # again."
        #
        # Instead, treat every callback as just a prompt to re-check
        # reality via isPlayingAudio()/_is_rainwave_stream(), and also
        # call this once a second from the main loop regardless of
        # any callback firing at all.
        try:
            is_playing = self.isPlayingAudio() and self._is_rainwave_stream()
        except Exception:
            is_playing = False

        if is_playing:
            self._not_playing_streak = 0
            if not self.active:
                self._activate()
            return

        # Require two consecutive "not playing" readings (this check
        # runs at most once a second) before actually deactivating.
        # isPlayingAudio() can itself read False for a single instant
        # around a brief internal player hiccup even while audio never
        # actually stops coming out of the speakers -- debouncing here
        # absorbs that without meaningfully delaying a real stop, which
        # still gets caught within ~2 seconds either way.
        self._not_playing_streak += 1
        if self._not_playing_streak >= 2 and self.active:
            self._deactivate()

    def _activate(self):
        self.active = True
        # Fresh tune-in: forget any leftover lag-buffer history from a
        # previous session so its first display isn't held back
        # waiting on stale data (see SyncQueue.reset()).
        self.sync_queue.reset()
        # The sync queue deliberately withholds the very first song
        # until it's caught up with the actual (buffered) audio --
        # see sync_queue.py -- which without this would just look like
        # the widget failed to load for ~15-20s. The skin shows a
        # "Tuning in..." placeholder for as long as this stays "true";
        # _pump_sync() below clears it the moment there's something
        # real to show instead.
        self.home.setProperty("Rainwave.Buffering", "true")
        self.home.setProperty(
            "Rainwave.SpinnerFrame", os.path.join(self.spinner_dir, "spinner_00.png")
        )
        now = time.time()
        song = self.widget.refresh(self._current_sid())
        self.sync_queue.push(song, now)
        self.dialog.display()
        # Nothing will actually be due yet at offset > 0 -- the widget
        # stays blank/previous-state until the buffer delay elapses,
        # same as the real audio does -- but this keeps the two code
        # paths (activation vs the regular poll below) identical
        # instead of duplicating the apply logic here.
        self._pump_sync(now)

    def _pump_sync(self, now):
        """Apply whichever polled snapshot has finished waiting out
        the configured stream buffer delay, if any (see sync_queue.py).
        Call this every tick, independent of the 5-second poll cadence
        -- the delay is usually longer than one poll interval, so a
        snapshot from a few polls back is often the one that's due.
        """
        song = self.sync_queue.poll(now)
        if song is None:
            return
        self.home.clearProperty("Rainwave.Buffering")
        self.widget.apply_current(song)
        self._apply_timing(song)
        self._update_player_info(song)
        # Same delayed data as everything else above -- in auto mode,
        # this is what keeps the background changing to match the
        # game whose audio is actually playing, not whichever game
        # the API most recently reported (see slideshow.py/game_art.py).
        # The song title is passed too, only actually used as a
        # fallback search signal if the album title alone can't find
        # a match -- see game_art.py's _resolve_game_id().
        self.slideshow.set_current_game(song.get("album"), song.get("title"))

    def onAVStarted(self):
        self._check_active_state()

    def _apply_timing(self, song):
        # song is None when refresh() had no usable data this cycle --
        # nothing to apply, keep the progress bar as it was.
        if song is None:
            return
        self.dialog.set_song_timing(
            song.get("start_actual"),
            song.get("length"),
            song.get("server_time"),
            self.sync_queue.offset,
        )

    def _update_player_info(self, song):
        # Keep the actual playing item's info tag current too, not just
        # the skin widget -- router.py sets this once at play time, but
        # the track (and therefore title/artist/album/art) changes every
        # few minutes as Rainwave moves on to the next song. Without this,
        # Kore (and any other JSON-RPC based remote) would keep showing
        # whatever song was playing when the station was first tuned in.
        if song is None:
            return
        if not self._is_rainwave_stream():
            return
        try:
            # Building a fresh, detached xbmcgui.ListItem() here and
            # calling updateInfoTag() on it looks reasonable and is
            # what Kodi's own official examples show, but in practice
            # (confirmed by multiple reports on the Kodi forums hitting
            # this exact symptom) title/artist/album set this way don't
            # reliably reach Player.GetItem/JSON-RPC -- only properties
            # like art (set via the separate setArt() call) get
            # through. The combination that actually works is fetching
            # the REAL currently-playing item via getPlayingItem(),
            # mutating its own music info tag in place, and passing
            # that same item back to updateInfoTag() -- not a new one.
            item = self.getPlayingItem()
            tag = item.getMusicInfoTag()
            tag.setTitle(song.get("title", ""))
            tag.setArtist(song.get("artist", ""))
            tag.setAlbum(song.get("album", ""))
            tag.setMediaType("song")

            # Without a duration, Kodi has nothing to compute a
            # percentage/progress from -- Player.GetProperties'
            # "totaltime" stays effectively unset, so Kore has no data
            # to draw a progress bar with at all (not a refresh
            # problem like title/artist, an actual missing-data one).
            length = song.get("length")
            if length:
                tag.setDuration(int(length))

            art = song.get("art", "")
            if art:
                item.setArt({"thumb": art, "icon": art})
            self.updateInfoTag(item)

            # Kodi's internal playback clock starts counting from 0
            # the moment *we* tuned in, not from wherever Rainwave
            # actually was in the track -- so without a seek, the
            # progress bar would be accurate in shape but wrong in
            # position (e.g. showing 0:15 elapsed on a track that
            # was actually already 2 minutes in). Only do this once
            # per song (tracked via _last_song_key), not on every
            # 5-second poll -- seeking repeatedly on an unchanged
            # song would cause an audible jump/stutter each time.
            #
            # Same re-basing as set_song_timing(): this method only
            # runs once the sync queue has decided the song is due for
            # display, `sync_queue.offset` seconds after the server
            # reported it -- so `server_time - start_actual` alone
            # would seek `offset` seconds further into the song than
            # what the listener is actually about to hear, right at
            # the moment it's applied. Subtracting the offset lines
            # the seek target up with the delayed display instead.
            #
            # This may simply do nothing on some Kodi versions/
            # configurations: IsLive=true (set in router.py, needed
            # to stop brief stalls being misread as end-of-track) can
            # also make Kodi refuse seeks on the grounds that a live
            # stream has no meaningful seek target. If so, the
            # progress bar will still render (from the duration set
            # above) but start counting from 0 each song rather than
            # the song's true elapsed position -- a cosmetic gap, not
            # a functional one.
            song_key = (song.get("title"), song.get("artist"), song.get("album"))
            if song_key != self._last_song_key:
                self._last_song_key = song_key
                start_actual = song.get("start_actual")
                server_time = song.get("server_time")
                if start_actual and server_time:
                    elapsed = max(0, server_time - start_actual - self.sync_queue.offset)
                    try:
                        self.seekTime(elapsed)
                    except Exception as e:
                        log(f"Could not seek to song position: {e}")
        except Exception as e:
            log(f"Could not update player info tag: {e}")

    def onPlayBackStopped(self):
        self._check_active_state()

    def onPlayBackEnded(self):
        self._check_active_state()

    def onPlayBackError(self):
        self._check_active_state()

    def _deactivate(self):
        if self.active:
            self.active = False
            self.dialog.hide_widget()
            self.widget.clear()
            self.home.clearProperty("Rainwave.Buffering")
            self.sync_queue.reset()
            # Playback has actually stopped, so allow the screensaver
            # to kick in again (it was inhibited in router.py while
            # a Rainwave stream was playing).
            xbmc.executebuiltin('InhibitScreensaver(false)')


def _reload_display_settings(home):
    # Small enough (one bool) not to warrant its own module -- mirrors
    # the same "read setting, write a window property, skin reads the
    # property" pattern Slideshow.reload_settings() uses. The skin's
    # previous/next panel is gated on Rainwave.ShowPrevNext via a
    # <visible> condition, so flipping this takes effect immediately,
    # no restart needed.
    enabled = xbmcaddon.Addon().getSettingBool("show_prev_next")
    home.setProperty("Rainwave.ShowPrevNext", "true" if enabled else "false")


def run():
    api = RainwaveAPI()
    widget = Widget(api)
    game_art = GameArtProvider()
    slideshow = Slideshow(game_art)
    sync_queue = SyncQueue()
    home = xbmcgui.Window(10000)
    _reload_display_settings(home)

    addon_path = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo("path"))
    # Every $INFO-bound texture elsewhere in this addon (slideshow
    # images, album art) is a full absolute path -- static references
    # written directly in the skin XML (like the settings gear icon)
    # get resolved against the skin's own media folder automatically,
    # but that resolution isn't guaranteed for a bare filename handed
    # to $INFO[Window(...).Property(...)] at runtime. Building the
    # full path here up front, once, rather than a bare filename each
    # tick, keeps this consistent with the pattern already proven to
    # work.
    spinner_dir = os.path.join(addon_path, "resources", "media")

    dialog = NowPlayingDialog(
        "script-rainwave-nowplaying.xml",
        xbmcaddon.Addon().getAddonInfo("path"),
        "Default",
        "1080i",
    )

    player_monitor = RainwavePlayerMonitor(widget, dialog, sync_queue, slideshow, spinner_dir)

    class SettingsMonitor(xbmc.Monitor):
        """Reloads settings-driven state whenever the user changes it,
        so a running Kodi session picks up changes immediately -- no
        restart required.
        """
        def onSettingsChanged(self):
            slideshow.reload_settings()
            sync_queue.reload_settings()
            _reload_display_settings(home)
            log("Settings changed, reloaded")

    kodi_monitor = SettingsMonitor()
    last_refresh = 0.0
    last_spinner_frame = 0.0
    spinner_index = 0

    log("Service started")

    while not kodi_monitor.abortRequested():
        now = time.time()

        player_monitor._check_active_state()

        if player_monitor.active:
            if now - last_refresh >= POLL_INTERVAL:
                song = widget.refresh(player_monitor._current_sid())
                sync_queue.push(song, now)
                last_refresh = now
            # Runs every TICK, not just on a poll: the buffer delay is
            # normally longer than POLL_INTERVAL, so the snapshot that
            # becomes due is usually one from a few polls back, and
            # checking every second is what makes the eventual display
            # update land close to the real audio transition instead
            # of up to POLL_INTERVAL seconds late.
            player_monitor._pump_sync(now)
            widget.tick(now)
            slideshow.tick(now)

            # Buffering placeholder's spinner (see
            # script-rainwave-nowplaying.xml) -- gated on its own
            # SPINNER_FRAME_INTERVAL timer rather than firing every
            # TICK directly, so the animation speed doesn't change if
            # TICK is ever tuned for other reasons. Only bothers
            # advancing while the placeholder is actually visible;
            # harmless either way since the control is hidden the
            # rest of the time, but no reason to keep writing a
            # property nobody's looking at.
            if home.getProperty("Rainwave.Buffering") == "true":
                if now - last_spinner_frame >= SPINNER_FRAME_INTERVAL:
                    spinner_index = (spinner_index + 1) % SPINNER_FRAME_COUNT
                    frame_path = os.path.join(spinner_dir, f"spinner_{spinner_index:02d}.png")
                    home.setProperty("Rainwave.SpinnerFrame", frame_path)
                    last_spinner_frame = now

        if kodi_monitor.waitForAbort(TICK):
            break

    if player_monitor.active:
        xbmc.executebuiltin('InhibitScreensaver(false)')
    dialog.hide_widget()
    log("Service stopped")


if __name__ == '__main__':
    run()
