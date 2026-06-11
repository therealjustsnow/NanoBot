# Music Commands

The full voice music player: playback, queue, filters, autoplay, and per-server playlists.

**32 commands.** Syntax: `[]` = optional, `<>` = required. Unless noted otherwise every command works as a slash command, a prefix command (default `n!`), and an @mention command.

**On this page:** [Playback](#playback) · [Playback Control](#playback-control) · [Queue Management](#queue-management) · [Player Settings](#player-settings) · [Info & Extras](#info--extras)

## Playback

Queue songs from YouTube or Spotify, search interactively, and connect to voice.

### `play`

Queue a song or playlist. Accepts YouTube URLs, Spotify links, or a search query.

- **Usage:** `/play [query]` · `n!play [query]`
- **Example:** `n!play lofi hip hop`
- **Permission:** None
- **Aliases:** `p`
- **Access:** Public · Slash + prefix

### `playnext`

Insert a track at the front of the queue, playing after the current song.

- **Usage:** `/playnext [query]` · `n!playnext [query]`
- **Permission:** None
- **Aliases:** `pn`
- **Access:** Public · Slash + prefix

### `playnow`

Skip the current track and start playing the given song immediately.

- **Usage:** `/playnow [query]` · `n!playnow [query]`
- **Permission:** None
- **Aliases:** `ps`
- **Access:** Public · Slash + prefix

### `stream`

Queue a livestream or direct media URL without buffering.

- **Usage:** `/stream [url]` · `n!stream [url]`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `shuffleplay`

Queue a playlist with its tracks pre-shuffled.

- **Usage:** `/shuffleplay [url]` · `n!shuffleplay [url]`
- **Permission:** None
- **Aliases:** `sp`
- **Access:** Public · Slash + prefix

### `search`

Search YouTube and pick a result from an interactive menu.

- **Usage:** `/search [query]` · `n!search [query]`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `join`

Connect the bot to your current voice channel.

- **Usage:** `/join` · `n!join`
- **Permission:** None
- **Aliases:** `summon`
- **Access:** Public · Slash + prefix

## Playback Control

Skip, pause, stop, and jump between tracks.

### `pause`

Pause playback.

- **Usage:** `/pause` · `n!pause`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `resume`

Resume paused playback.

- **Usage:** `/resume` · `n!resume`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `skip`

Vote-skip the current track. The requester or a user with Manage Server skips immediately.

- **Usage:** `/skip` · `n!skip`
- **Permission:** None
- **Aliases:** `s`
- **Access:** Public · Slash + prefix

### `forceskip`

Force-skip the current track immediately, bypassing the vote requirement.

- **Usage:** `/forceskip` · `n!forceskip`
- **Permission:** Manage Server
- **Aliases:** `fs`
- **Access:** Restricted · Slash + prefix

### `jump`

Skip ahead to a specific position in the queue.

- **Usage:** `/jump [position]` · `n!jump [position]`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `stop`

Stop playback, clear the queue, and disconnect the bot from the voice channel.

- **Usage:** `/stop` · `n!stop`
- **Permission:** None
- **Aliases:** `dc`
- **Access:** Public · Slash + prefix

## Queue Management

View, reorder, remove, and export the playback queue.

### `queue`

Show the upcoming queue.

- **Usage:** `/queue` · `n!queue`
- **Permission:** None
- **Aliases:** `q`
- **Access:** Public · Slash + prefix

### `move`

Move a track from one queue position to another.

- **Usage:** `/move [from] [to]` · `n!move [from] [to]`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `remove`

Remove a single track from the queue by its position number.

- **Usage:** `/remove [position]` · `n!remove [position]`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `clear`

Clear all tracks from the queue without stopping the current song.

- **Usage:** `/clear` · `n!clear`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `shuffle`

Randomly shuffle all tracks currently in the queue.

- **Usage:** `/shuffle` · `n!shuffle`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `pldump`

Export all queued track URLs to a text file.

- **Usage:** `/pldump` · `n!pldump`
- **Permission:** None
- **Access:** Public · Slash + prefix

## Player Settings

Volume, speed, audio effects, loop modes, and seeking.

### `volume`

Set the playback volume (0–200). Defaults to 100.

- **Usage:** `/volume [level]` · `n!volume [level]`
- **Example:** `n!volume 150`
- **Permission:** None
- **Aliases:** `vol`
- **Access:** Public · Slash + prefix

### `speed`

Set the playback speed multiplier (0.5–3.0).

- **Usage:** `/speed [multiplier]` · `n!speed [multiplier]`
- **Example:** `n!speed 1.5`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `filter`

Apply an audio effect to the current track. Available effects include bassboost, nightcore, vaporwave, and more.

- **Usage:** `/filter [effect]` · `n!filter [effect]`
- **Example:** `n!filter bassboost`
- **Permission:** None
- **Aliases:** `fx`
- **Access:** Public · Slash + prefix

### `loop`

Cycle through loop modes: off → track → queue.

- **Usage:** `/loop` · `n!loop`
- **Permission:** None
- **Aliases:** `repeat`
- **Access:** Public · Slash + prefix

### `seek`

Jump to a specific timestamp in the current track.

- **Usage:** `/seek [time]` · `n!seek [time]`
- **Example:** `n!seek 1:30`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `replay`

Restart the current track from the beginning.

- **Usage:** `/replay` · `n!replay`
- **Permission:** None
- **Access:** Public · Slash + prefix

## Info & Extras

Now Playing card, lyrics, autoplaylist, and more.

### `nowplaying`

Show the live Now Playing card with track info and progress.

- **Usage:** `/nowplaying` · `n!nowplaying`
- **Permission:** None
- **Aliases:** `np`
- **Access:** Public · Slash + prefix

### `lyrics`

Fetch lyrics for the currently playing track.

- **Usage:** `/lyrics` · `n!lyrics`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `grab`

DM yourself the current track's info and URL.

- **Usage:** `/grab` · `n!grab`
- **Permission:** None
- **Aliases:** `save`
- **Access:** Public · Slash + prefix

### `autoplay`

Toggle smart autoplay — queues YouTube-related tracks when the queue empties. Requires a YouTube track to have played first to seed the mix.

- **Usage:** `/music autoplay` · `n!autoplay`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `guildplay`

Toggle guild-playlist mode — keeps playing from the server's saved guild playlist when the queue empties.

- **Usage:** `/music guildplay` · `n!guildplay`
- **Permission:** None
- **Access:** Public · Slash + prefix

### `guildplaylist`

Manage the server's persistent guild playlist. Subcommands: add, remove, list. clear requires Manage Server.

- **Usage:** `/guildplaylist add [url]`
- **Permission:** None (clear requires Manage Server)
- **Aliases:** `gpl`
- **Access:** Public · Slash

### `follow`

Make the bot follow you when you switch voice channels.

- **Usage:** `/follow` · `n!follow`
- **Permission:** None
- **Access:** Public · Slash + prefix
