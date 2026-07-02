# plugin.audio.rainwave
A simple Kodi addon for Rainwave Internet Radio.

------------------------------

Version 1.0.0.

An addon for Kodi based on the (https://rainwave.cc/api4/)[Rainwave Api] for playing the different stations (All, Overclocked Remixes, Chiptunes etc.) and display a dialog box akin to the (https://rainwave.cc/twitch)[Twitch Widget].

Some pointers:

* Screensaver is disabled with the `xbmc.executebuiltin('InhibitScreensaver(true)')` subroutine in router.py. Comment the line, erase it or change the bool to "false" to change the setting.
* Artworks for the station are not provided. You can add them to the `skins/media` subfolder. The names must match the one given in `constants.py`
* The display box can be edited through `script-rainwave-nowplaying.xml` in the `skins/Default/1080i` subfolder. See the (https://kodi.wiki/view/Add-on_development)[KodiWiki] for further documentation.

As I am a complete newbie for python and kodi programming, I do not know if I will update this project. Don't hesitate to fork and expand it if you like!
