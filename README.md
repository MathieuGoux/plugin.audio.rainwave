# plugin.audio.rainwave
A simple Kodi addon for Rainwave Internet Radio.

------------------------------

Version 3.0.1.

An addon for Kodi based on the [Rainwave Api](https://rainwave.cc/api4/) for playing the different stations (All, Overclocked Remixes, Chiptunes etc.) and displaying a dialog box akin to the [Twitch Widget](https://rainwave.cc/twitch) for the current, previously played and next songs.

Some pointers:

* Screensaver is disabled with the `xbmc.executebuiltin('InhibitScreensaver(true)')` subroutine in `router.py`. Comment the line, erase it or change the bool to "false" to change the setting.
* Artworks for the stations are not provided. You can add them to the `skins/media` subfolder. The names must match the ones given in `constants.py` with a `.png` extension.
* The display boxes can be edited through `script-rainwave-nowplaying.xml` in the `skins/Default/1080i` subfolder. See the [KodiWiki](https://kodi.wiki/view/Add-on_development) for further documentation.
* Settings allow to define a background images during playback, with two main settings:

  * Local folder: define a local image folder.
  * Automatic (fetch by game): need a [SteamGridDB](https://steamgriddb.com) account and an API-key (once connected, `Preferences > API tab`). Fetching is based on the song metadata and automatically retrieves "heroes" artworks to display during playback. Fetching mode is a fuzzy search: if there is no exact match, the script erases the last words of the album title until something clicks. Otherwise, it loops back to the local folder solution. Artworks are locally stored in the `userdata/addon_data/plugin.audio.rainwave/art_cache` folder. You can set a limit for the size of the folder (0 = unlimited, otherwise the oldest artworks downloaded are erased first) or erase all of them at once with the "Clear Cache" button.

* Settings allow to enable / disable the Coming Up / Previously played box. A folder next to the stations one allows access to the 4 previously played songs on a given channel. Future update will better integrate it into the UI.
* Authentification through the Rainwave API doesn't work, as Rainwave pivoted to a discord-only auth some years ago. As such, there seems to be no way to rate / request songs.
* The "Coming Up" next info roll through all candidates of the current polls, as there is no live update of the voting counts.
* There is a buffer of 5~15 seconds before audio playback, and thus a discrepancy between audio and the song metadata (artwork, title, progress bar, etc.). You can circumvent it with a "Playback Sync" setting. The first option automatically synchronizes audio with the song metadata, and the slider allows to define a buffer delay in seconds to further tweak the playback. Be aware that if you checked the "Sync to actual audio", playback will start a few seconds after launching the app, so don't worry! I added a "Tuning in..." placeholder with a spinner to further communicate than the wait is not a freeze.

Have fun!

-----------------------------------

How to install on Kodi:

1. Download the main folder into a zip file
2. In Kodi, allow addon install from all sources
3. Select the zip file, and voilà!

