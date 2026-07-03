# plugin.audio.rainwave
A simple Kodi addon for Rainwave Internet Radio.

------------------------------

Version 2.0.0.

An addon for Kodi based on the [Rainwave Api](https://rainwave.cc/api4/) for playing the different stations (All, Overclocked Remixes, Chiptunes etc.) and displaying a dialog box akin to the [Twitch Widget](https://rainwave.cc/twitch).

Some pointers:

* Screensaver is disabled with the `xbmc.executebuiltin('InhibitScreensaver(true)')` subroutine in `router.py`. Comment the line, erase it or change the bool to "false" to change the setting.
* Artworks for the stations are not provided. You can add them to the `skins/media` subfolder. The names must match the ones given in `constants.py` with a `.png` extension.
* The display box can be edited through `script-rainwave-nowplaying.xml` in the `skins/Default/1080i` subfolder. See the [KodiWiki](https://kodi.wiki/view/Add-on_development) for further documentation.
* Settings allow to define a background folder, to display images during playback. 

As I am a complete newbie for python and kodi programming, I do not know if I will update this project (with authentification, rating, votes, etc., cf. API documentation). Don't hesitate to fork and expand it if you like!

-----------------------------------

How to install on Kodi:

1. Download the main folder into a zip file
2. In Kodi, allow addon install from all sources
3. Select the zip file, and voilà!

