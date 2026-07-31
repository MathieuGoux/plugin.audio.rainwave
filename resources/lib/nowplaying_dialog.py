import threading
import time

import xbmc
import xbmcaddon
import xbmcgui

from .utils import log

# Control IDs of the progress bar / time label
PROGRESS_CONTROL_ID = 501
TIME_LABEL_CONTROL_ID = 502

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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._visible = False
        self._monitor = xbmc.Monitor()
        self._progress_thread = None
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
            self._progress_thread = None
            self.close()
            log("Now-playing widget hidden")

    @property
    def is_visible(self):
        return self._visible

    def set_song_timing(self, start_actual, length, server_time, offset=0):
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
            pass

    @staticmethod
    def _format_time(seconds):
        seconds = int(seconds)
        return "{0}:{1:02d}".format(seconds // 60, seconds % 60)

    def onAction(self, action):
        # Open game selector when "i" key is pressed
        if action.getId() == xbmcgui.ACTION_SHOW_INFO:
            self._open_game_selector()
            return

        # Open settings on context menu
        if action.getId() == xbmcgui.ACTION_CONTEXT_MENU:
            xbmc.executebuiltin(f"Addon.OpenSettings({ADDON_ID})")
            return

        # Forward all other actions to underlying UI
        name = FORWARDED_ACTIONS.get(action.getId())
        if name:
            target = xbmcgui.getCurrentWindowId()
            xbmc.executebuiltin(f"Action({name},{target})")
            
    def _open_game_selector(self):
        """Open game selector and update manifest with manual override."""
        try:
            from .game_selector import GameSelectorDialog
            addon = xbmcaddon.Addon()
            # Get current album and song from the now-playing widget
            current_album = xbmcgui.Window(10000).getProperty("Rainwave.Album")
            current_song_title = xbmcgui.Window(10000).getProperty("Rainwave.Title")
            current_sid = xbmcgui.Window(10000).getProperty("Rainwave.CurrentStation")
            current_sid = int(current_sid) if current_sid else None
            current_game = xbmcgui.Window(10000).getProperty("Rainwave.ResolvedGame")

            dialog = GameSelectorDialog(
                addon.getSettingString("steamgriddb_api_key"),
                current_album=current_album,
                current_sid=current_sid,
                current_song_title=current_song_title,
                current_game=current_game
            )
            if dialog.show():
                # Update slideshow immediately
                xbmcgui.Window(10000).setProperty(
                    "Rainwave.ManualGame",
                    dialog.selected_game.get("name", "")
                )
        except Exception as e:
            log(f"Game selector failed: {e}")

    def onClick(self, control_id):
        pass