import xbmc
import xbmcaddon
import xbmcgui

from resources.lib.api import RainwaveAPI
from resources.lib.widget import Widget
from resources.lib.nowplaying_dialog import NowPlayingDialog
from resources.lib.utils import log

POLL_INTERVAL = 5  # seconds
STREAM_HOST = "relay.rainwave.cc"


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

    def __init__(self, widget, dialog):
        super().__init__()
        self.widget = widget
        self.dialog = dialog
        self.active = False
        self.home = xbmcgui.Window(10000)

    def _is_rainwave_stream(self):
        try:
            return STREAM_HOST in self.getPlayingFile()
        except Exception:
            return False

    def _current_sid(self):
        sid = self.home.getProperty("Rainwave.CurrentStation")
        return int(sid) if sid else None

    def onAVStarted(self):
        if self._is_rainwave_stream():
            self.active = True
            song = self.widget.refresh(self._current_sid())
            self._apply_timing(song)
            self.dialog.display()

    def _apply_timing(self, song):
        self.dialog.set_song_timing(
            song.get("start_actual"),
            song.get("length"),
            song.get("server_time"),
        )

    def onPlayBackStopped(self):
        self._deactivate()

    def onPlayBackEnded(self):
        self._deactivate()

    def onPlayBackError(self):
        self._deactivate()

    def _deactivate(self):
        if self.active:
            self.active = False
            self.dialog.hide_widget()
            self.widget.clear()


def run():
    api = RainwaveAPI()
    widget = Widget(api)

    dialog = NowPlayingDialog(
        "script-rainwave-nowplaying.xml",
        xbmcaddon.Addon().getAddonInfo("path"),
        "Default",
        "1080i",
    )

    player_monitor = RainwavePlayerMonitor(widget, dialog)
    kodi_monitor = xbmc.Monitor()

    log("Service started")

    while not kodi_monitor.abortRequested():
        if player_monitor.active:
            song = widget.refresh(player_monitor._current_sid())
            player_monitor._apply_timing(song)

        if kodi_monitor.waitForAbort(POLL_INTERVAL):
            break

    dialog.hide_widget()
    log("Service stopped")


if __name__ == '__main__':
    run()
