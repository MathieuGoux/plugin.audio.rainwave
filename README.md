# plugin.audio.rainwave

**A Kodi addon for Rainwave Internet Radio**

[Kodi](https://kodi.tv)

[License: GPL-3.0](https://www.gnu.org/licenses/gpl-3.0)

---

## **Table of Contents**

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Customization](#customization)
- [Technical Details](#technical-details)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [Acknowledgments](#acknowledgments)
- [License](#license)

---

[Rainwave](https://rainwave.cc/game/) is an Internet web radio specialized in video game music. This Kodi addon allows audio playback on all 6 stations, with information widgets and customizable background slideshows.

## **Features**

### Main Functionalities

- **Stream all Rainwave stations** directly within Kodi
- **Metadata display** showing current, previous, and upcoming songs
- **Album artwork** automatically fetched and displayed
- **Real-time updates** as songs change on the stream

### Visual Enhancements

- **Now Playing widget** with song information and progress bar
- **Coming Up panel** showing next song candidates
- **Previously Played panel** showing the last song
- **Customizable background slideshow** with multiple modes

### Audio Features

- **Automatic stream buffer synchronization** to align metadata with audio
- **Configurable buffer delay** for fine-tuning synchronization
- **Screensaver inhibition** during playback (configurable)
- **Continuous playback** without interruptions

### Artwork System

- **Automatic game artwork fetching** from [SteamGridDB](https://www.steamgriddb.com/)
- **Local folder support** for custom backgrounds
- **Random artwork mode** from cached game art
- **Manual game selection** override for accurate artwork
- **Artwork caching** with configurable limits

---

## **Installation**

1. **Download the addon**:
   - Click the green "Code" button on GitHub
   - Select "Download ZIP"
   - Save to a location accessible from Kodi

2. **Enable unknown sources in Kodi**:
   - Go to **Settings** > **System** > **Add-ons**
   - Enable **"Unknown sources"**

3. **Install the addon**:
   - Go to **Add-ons** > **Install from zip file**
   - Select the downloaded ZIP file

4. **Access the addon**:
   - Go to **Add-ons** > **My add-ons** > **Audio add-ons** > **Rainwave**

---

## **Usage**

### Basic Usage

1. **Launch the addon** from Kodi home screen or addons menu

2. **Select a station** from the main menu
   - **All** - All music genres mixed
   - **Game** - Video game music
   - **OC Remix** - OverClocked ReMix tracks
   - **Covers** - Video game music covers
   - **Chiptune** - Chiptune and 8-bit music
   - **Chill** - Relaxing video game music

3. **Use Kodi's standard playback control** (play, pause, stop, mute...)

4. Press **"i" (Information)** key to manually select game artwork (if "Background source" is set to "Automatic" in the settings)

### Navigation

   - **Station Menu:** selection any station to start playback
   - **History:** view rencently played songs
   - **Settings**

---

## **Configuration**

Access settings from the addon menu or via Kodi's addon settings.

### Background Artwork Modes

#### 1. Local Folder Mode
   - Set `Slideshow Source` = Local folder
   - Configure `Slideshow Path` to your image folder
   - Cycles through all images in the folder

#### 2. Automatic Mode (SteamGridDB)
   - Requires SteamGridDB API key (get from [steamgriddb.com](https://steamgriddb.com))
   - Automatically fetches hero artwork for currently playing game
   - Uses album title as primary search, then look for clues in song title if no perfect match is found (for "Game" station, only uses album title for the search, as it is always the exact game name)
      - To correct the artworks automatically downloaded, press the `Information Key` (`i` by default) to manually override the correct game to choose from, for the entire album or for a given song in case of a compilation album. Overrides should apply during playback.
   - You can set the maximum number or artworks to retrieve, from 1 to "10" or choose to download all artworks from a given game. Except for the "all" setting, you can choose to download artworks from a sibling game (i.e., "Earthworm Jim 2" for "Earthworm Jim" and vice versa) to try to attain the set number.

#### 3. Random Mode
   - Uses cached game artwork from SteamGridDB
   - Refreshes with new batch every 2 minutes

### Playback Synchronization
   - **Stream Sync Enabled**: Enable automatic synchronization
   - **Stream Sync Offset**: Fine-tune delay (0-60 seconds)

### Screensaver Control
   - **Inhibit Screensaver**: Prevent screensaver during playback (default: on)

---

## **Customization**

### Station Artwork
Add custom station icons to `resources/skins/media/` (names must match the ones in `constants.py`)
   - `Game.png` - Game station
   - `OC Remix.png` - OC Remix station
   - `Covers.png` - Covers station
   - `Chiptune.png` - Chiptune station
   - `All.png` - All station
   - `Chill.png` - Chill station

### UI Customization
Edit `resources/skins/Default/1080i/script-rainwave-nowplaying.xml`
See [Kodi Wiki](https://kodi.wiki/view/Add-on_development) for skinning documentation.

### Available Window Properties
The addon exposes the following properties on Window(10000) for skin customization:

   1. Current Song:
      - Rainwave.Title - Song title
      - Rainwave.Artist - Artist name
      - Rainwave.Album - Album name
      - Rainwave.Art - Album artwork URL
      - Rainwave.Station - Station name

   2. Next Songs:
      - Rainwave.NextTitleA, Rainwave.NextTitleB - Next song titles (A/B slots)
      - Rainwave.NextArtistA, Rainwave.NextArtistB - Next song artists
      - Rainwave.NextAlbumA, Rainwave.NextAlbumB - Next song albums
      - Rainwave.NextArtA, Rainwave.NextArtB - Next song artwork
      - Rainwave.NextActive - Active slot ("A" or "B")

   3. Previous Song:
      - Rainwave.PreviousTitle - Previous song title
      - Rainwave.PreviousArtist - Previous song artist
      - Rainwave.PreviousAlbum - Previous song album
      - Rainwave.PreviousArt - Previous song artwork

   4. Slideshow:
      - Rainwave.SlideshowPath - Slideshow source indicator
      - Rainwave.SlideshowActive - Active slideshow slot ("A" or "B")
      - Rainwave.SlideshowImageA, Rainwave.SlideshowImageB - Image paths

   5. Status:
      - Rainwave.Buffering - "true" when buffering (shows spinner)
      - Rainwave.SpinnerFrame - Current spinner animation frame path
      - Rainwave.ShowPrevNext - "true" if previous/next panels should be shown

---

## **Technical Details**

### Architecture
The addon uses a dual-process architecture:
      
   1. Plugin Process (`default.py` + `router.py`):
      - Handles user interactions (station selection, navigation)
      - Initiates playback
      - Short-lived (one instance per user action)
      
   2. Service Process (`service.py`):
      - Long-running background process
      - Monitors playback state
      - Polls Rainwave API for updates
      - Updates now-playing display
      - Manages slideshow
      - Starts automatically with Kodi

### Communication Between Processes
Since Kodi creates a new Python interpreter for each plugin invocation, the plugin and service processes communicate via Window(10000) properties:

   - Rainwave.CurrentStation - Set by plugin when a station is selected
   - Other properties for displaying metadata

### Stream Buffer Handling
Rainwave's relay stream has a 15-20 second buffer, causing a delay between when the API reports a song is playing and when it's actually audible. To circumvent the issue, the `SyncQueue class` implements a lag buffer that:
      
   1. Stores polled API responses with timestamps
   2. Delays applying metadata until it matches the audio position
   3. Automatically compensates for the stream buffer delay

This ensures the display stays in sync with what the user is actually hearing.

### API Integration

#### Rainwave API

   - Base URL: https://rainwave.cc/api4/
   - Endpoints Used:
      - info - Get station information and current/next/previous songs
      - tune_in - Register as a listener (currently 404s for anonymous users)
      - Authentication: Not required for basic playback (Rainwave uses Discord-only auth)
   
   - Rate Limiting: Respectful polling (5-second intervals)

#### SteamGridDB API

   - Purpose: Fetch game artwork for background slideshow
   - Requirements: User-provided API key
   - Endpoints Used:
      - Search for games by title
      - Fetch hero artwork for games
   
   - Caching: All fetched artwork is cached locally for offline use

---

## **Limitations**

### API Limitations

   - No Authentication: Rainwave's API authentication doesn't work with Discord-only auth
   - No Song Rating: Cannot rate songs through the API
   - No Song Requests: Cannot request specific songs
   - Vote Counts: "Coming Up" panel shows all candidates but cannot display live vote counts

### Technical Limitations

   - Stream Buffer: 5-15 second delay before audio starts (inherent to streaming)
   - Progress Bar: May show slight discrepancy due to buffer synchronization
   - ICY Metadata: Stream's embedded metadata is disabled to prevent overwriting custom metadata

### Feature Limitations

   - No User History: Cannot access user-specific playback history (requires authentication)
   - No Favorites: Cannot save favorite songs or stations

---

## **Contributing**

I don't intend to actively maintain this project or investigate any future issues. **The addon is provided as is**. I may occasionally update it to fix a bug or address a specific issue, but I won't be monitoring user questions, feature requests, or bug reports.

It was a fun little project, and I'm happy to share it; but that's as far as I intend to take it.

That said, you're more than welcome to fork the repository, add new features, fix bugs, improve the code, or otherwise make it your own.

---

## **License**
This project is licensed under the GNU General Public License v3.0 or later.

Copyright (C) 2026 Mathieu Goux (MG)

*This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version. This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details. You should have received a copy of the GNU General Public License along with this program.  If not, see <https://www.gnu.org/licenses/>.*

---

## **Acknowledgments**

   - My wonderful spouse, for helping me decipher some of the more arcane Python syntax that had me scratching my head.
   - My friends, who helped me design the dual-process architecture.
   - The [RadioParadise addon by Alexander Dietrich](https://kodi.tv/addons/omega/script.radioparadise/) and the [Radioplayer France addon by sy6sy2](https://kodi.tv/addons/omega/plugin.audio.radioplayerfrance/), which provided a solid foundation for implementing API integration and live audio streaming in Kodi.

### IMPORTANT NOTE
I used AI (primarily Claude and Mistral) to help develop and debug the `SyncQueue` implementation and, in particular, the `game_art` and `game_selector` modules.

I completely understand if that's a deal-breaker for some people. I avoided relying on AI for as long as I could, but eventually I reached the limits of my Python knowledge, and so did my wife's and my friends'. I reviewed, tested, and integrated every AI-generated suggestion myself rather than accepting its output blindly and can confirm that the addon works as intended.

---

*Last updated: August 05, 2026*

*Version: 3.0.8*
