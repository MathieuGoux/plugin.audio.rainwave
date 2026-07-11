# plugin.audio.rainwave
A simple Kodi addon for Rainwave Internet Radio.

------------------------------

Version 2.2.0.

An addon for Kodi based on the [Rainwave Api](https://rainwave.cc/api4/) for playing the different stations (All, Overclocked Remixes, Chiptunes etc.) and displaying a dialog box akin to the [Twitch Widget](https://rainwave.cc/twitch) for the current, previously played and next songs.

Some pointers:

* Screensaver is disabled with the `xbmc.executebuiltin('InhibitScreensaver(true)')` subroutine in `router.py`. Comment the line, erase it or change the bool to "false" to change the setting.
* Artworks for the stations are not provided. You can add them to the `skins/media` subfolder. The names must match the ones given in `constants.py` with a `.png` extension.
* The display boxes can be edited through `script-rainwave-nowplaying.xml` in the `skins/Default/1080i` subfolder. See the [KodiWiki](https://kodi.wiki/view/Add-on_development) for further documentation.
* Settings allow to define a background folder, to display images during playback.
* Settings allow to enable / disable the Coming Up / Previously played box. 
* Authentification through API doesn't work, as Rainwave pivoted to a discord-only auth some years ago. As such, there seems to be no way to rate / request songs.
* The "Coming Up" next info roll through all candidates of the current polls, as there is no live update of the voting counts.
* There is a buffer of 5~15 seconds before audio playback, and thus a discrepancy between audio and the song metadata (artwork, title, progress bar, etc.). I didn't find a way to reduce it or circumvent it alas (maybe in a future revision of the app).

Have fun!

-----------------------------------

How to install on Kodi:

1. Download the main folder into a zip file
2. In Kodi, allow addon install from all sources
3. Select the zip file, and voilà!

