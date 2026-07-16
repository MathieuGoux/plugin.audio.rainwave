import threading
import time

import xbmc
import xbmcgui

from .utils import log

# Control IDs of the progress bar / time label added to
# script-rainwave-nowplaying.xml. Kept here rather than hardcoded
# inline so the skin and the Python side can't silently drift apart.
PROGRESS_CONTROL_ID = 501
TIME_LABEL_CONTROL_ID = 502

# Kodi addon dialogs always become the exclusive input target while
# shown -- this is true even with zero focusable controls, so we can't
# just rely on the window "having nothing to focus." Instead we catch
# every navigation action here and immediately replay it against the
# window underneath via the Action() builtin, so browsing feels
# unaffected by the widget being on screen.
#
# ACTION_CONTEXT_MENU is the one exception: rather than forwarding it,
# it opens this addon's own settings -- "bring up the context menu for
# whatever's on screen" naturally lands on the widget that's actually
# on top, and it's the only way a remote/keyboard-only user can reach
# settings without leaving the now-playing view (mouse/touch users
# also have the on-screen gear icon, see script-rainwave-nowplaying.xml).
#
# This only covers keyboard/remote/gamepad navigation, not mouse
# clicks/drags -- Kodi has no equivalent generic "replay this mouse
# event elsewhere" builtin. For remote-driven HTPC use this covers
# the common case.
FORWARDED_ACTIONS = {
    xbmcgui.ACTION_MOVE_LEFT: "Left",
    xbmcgui.ACTION_MOVE_RIGHT: "Right",
    xbmcgui.ACTION_MOVE_UP: "Up",
    xbmcgui.ACTION_MOVE_DOWN: "Down",
    xbmcgui.ACTION_PAGE_UP: "PageUp",
    xbmcgui.ACTION_PAGE_DOWN: "PageDown",
    xbmcgui.ACTION_SELECT_ITEM: "Select",
    xbmcgui.ACTION_PARENT_DIR: "ParentDir",
    xbmcgui.ACTION_PREVIOUS_MENU: "Back",
    xbmcgui.ACTION_SHOW_INFO: "Info",
    xbmcgui.ACTION_NEXT_ITEM: "NextItem",
    xbmcgui.ACTION_PREV_ITEM: "PreviousItem",
}

ADDON_ID = "plugin.audio.rainwave"


class NowPlayingDialog(xbmcgui.WindowXMLDialog):
    """Persistent 'now playing' overlay.

    IMPORTANT: this must be shown with show(), never doModal(). show()
    displays the window on top of the current UI and returns
    immediately, leaving the user free to keep navigating Kodi.

    All of its labels/images are bound via $INFO[Window(10000)...]
    expressions in the skin XML, so once shown it updates itself
    automatically whenever those window properties change -- no need
    to push data into the dialog directly.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._visible = False
        self._monitor = xbmc.Monitor()
        self._progress_thread = None

        # Timing reference for the current song, all in the
        # Rainwave *server's* clock -- see set_song_timing().
        self._song_start = None
        self._song_length = None
        self._clock_offset = 0.0
        self._display_offset = 0

    def display(self):
        if not self._visible:
            self.show()
            self._visible = True
            self._start_progress_thread()
            log("Now-playing widget shown")

    def hide_widget(self):
        if self._visible:
            self._visible = False
            self._progress_thread = None  # loop below exits on its own
            self.close()
            log("Now-playing widget hidden")

    @property
    def is_visible(self):
        return self._visible

    def set_song_timing(self, start_actual, length, server_time, offset=0):
        """Feed fresh timing data from the API into the progress bar.

        start_actual and server_time both come from Rainwave's own
        clock (sched_current.start_actual / api_info.time), so we
        derive the offset from our local clock once here rather than
        assuming the Kodi box's system clock matches the server's --
        that keeps the bar accurate even if the box's clock is off.
        Safe to call repeatedly as the same song keeps polling; the
        bar just keeps ticking, no jump.

        `offset` is the configured stream-sync delay (see
        sync_queue.py) in seconds. Without it, elapsed time here is
        computed against the server's *true* clock -- but this method
        itself is only called `offset` seconds after that clock event,
        via the sync queue, so the bar would already read `offset`
        seconds in on the very song it just switched to, and would
        hit 100% (and freeze there) `offset` seconds before the
        listener actually reaches the end of the song. Subtracting
        `offset` from every elapsed-time calculation re-bases the
        clock onto "what the listener is actually hearing right now"
        instead of "what the server says is happening right now",
        which is what the progress bar should represent.
        """
        if not start_actual or not length:
            return
        self._song_start = start_actual
        self._song_length = length
        self._clock_offset = server_time - time.time()
        self._display_offset = offset

    def _start_progress_thread(self):
        if self._progress_thread and self._progress_thread.is_alive():
            return
        self._progress_thread = threading.Thread(
            target=self._progress_loop, daemon=True
        )
        self._progress_thread.start()

    def _progress_loop(self):
        while self._visible and not self._monitor.abortRequested():
            self._update_progress()
            if self._monitor.waitForAbort(1):
                break

    def _update_progress(self):
        if not self._song_start or not self._song_length:
            return

        now_server = time.time() + self._clock_offset - self._display_offset
        elapsed = max(0.0, now_server - self._song_start)
        elapsed = min(elapsed, self._song_length)
        percent = (elapsed / self._song_length) * 100

        try:
            self.getControl(PROGRESS_CONTROL_ID).setPercent(percent)
            self.getControl(TIME_LABEL_CONTROL_ID).setLabel(
                "{0} / {1}".format(
                    self._format_time(elapsed), self._format_time(self._song_length)
                )
            )
        except RuntimeError:
            # Window/controls not fully initialised yet -- next tick
            # a second later will pick it back up.
            pass

    @staticmethod
    def _format_time(seconds):
        seconds = int(seconds)
        return "{0}:{1:02d}".format(seconds // 60, seconds % 60)

    def onAction(self, action):
        if action.getId() == xbmcgui.ACTION_CONTEXT_MENU:
            xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
            return
        name = FORWARDED_ACTIONS.get(action.getId())
        if name:
            target = xbmcgui.getCurrentWindowId()
            xbmc.executebuiltin(f"Action({name},{target})")
