import sys
import time

import xbmcgui
import xbmcplugin

from .constants import STATIONS
from .artwork import Artwork


def _relative_time(played_at):
    """"12 min ago" style formatting for a unix timestamp, or "" if
    there's nothing to show. Purely cosmetic for this read-only list,
    so a plain wall-clock delta (no sync-queue-style correction for
    Kodi/server clock drift, unlike service.py's audio-sync math) is
    more than accurate enough.
    """
    if not played_at:
        return ""
    delta = max(0, int(time.time() - played_at))
    if delta < 60:
        return "just now"
    minutes = delta // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


class HistoryMenu:
    """Read-only "recently played" browser, station by station.

    Rainwave is a live radio stream, not an on-demand catalog -- past
    songs can't actually be replayed through this addon (or the
    station itself), so every entry here is informational only, never
    playable. See api.py's get_history() for where the data comes
    from and why it's a handful of recent plays rather than a deep
    history: the fuller playback_history endpoint needs a logged-in
    user's own account credentials, which this addon doesn't collect.
    """

    def __init__(self, handle, api):
        self.handle = handle
        self.api = api
        self.art = Artwork()

    def show_stations(self):
        base_url = sys.argv[0]

        for sid, name in STATIONS.items():
            url = f"{base_url}?action=history_songs&id={sid}"
            item = xbmcgui.ListItem(label=name)
            item.setArt({
                "thumb": self.art.station(name),
                "icon": self.art.icon(),
                "fanart": self.art.fanart(),
            })
            xbmcplugin.addDirectoryItem(
                handle=self.handle,
                url=url,
                listitem=item,
                isFolder=True,
            )

        xbmcplugin.endOfDirectory(self.handle)

    def show_songs(self, sid):
        xbmcplugin.setContent(self.handle, "songs")

        history = self.api.get_history(sid)

        if not history:
            # Most likely a transient API hiccup (see api.py) rather
            # than a station that's truly never played anything --
            # worth saying so rather than just showing an empty list,
            # which looks identical to "this feature is broken".
            item = xbmcgui.ListItem(label="No history available right now")
            item.setProperty("IsPlayable", "false")
            xbmcplugin.addDirectoryItem(self.handle, sys.argv[0], item, False)
            xbmcplugin.endOfDirectory(self.handle)
            return

        for song in history:
            title = song.get("title") or "Unknown"
            artist = song.get("artist", "")
            album = song.get("album", "")
            when = _relative_time(song.get("played_at"))

            item = xbmcgui.ListItem(label=title)
            item.setLabel2(artist)
            # Explicitly non-playable -- without this, some skins'
            # list views try to resolve a click as playback by
            # default, which would just fail (there's no stream URL
            # for a past song) and show the user an error for what's
            # meant to be a purely informational entry.
            item.setProperty("IsPlayable", "false")

            tag = item.getMusicInfoTag()
            tag.setTitle(title)
            tag.setArtist(artist)
            tag.setAlbum(album)
            tag.setMediaType("song")

            art = song.get("art", "")
            item.setArt({"thumb": art, "icon": self.art.icon()} if art else {"icon": self.art.icon()})

            detail = " / ".join(part for part in (artist, album, when) if part)
            if detail:
                item.setLabel(f"{title}   [COLOR=FF999999]{detail}[/COLOR]")

            xbmcplugin.addDirectoryItem(
                handle=self.handle,
                url=sys.argv[0],
                listitem=item,
                isFolder=False,
            )

        xbmcplugin.endOfDirectory(self.handle)
