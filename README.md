# Sona

A small Python Discord bot that joins a voice channel and streams audio from a
YouTube URL. It uses `yt-dlp` to resolve the media URL and FFmpeg to supply
audio to Discord.

## Setup

1. Install [FFmpeg](https://ffmpeg.org/). On macOS: `brew install ffmpeg`.
2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`, then add the bot token from the Discord
   Developer Portal.
4. Invite the app to your server with the `bot` and `applications.commands`
   scopes. Give it `Connect` and `Speak` permissions.
5. Start the bot:

   ```bash
   python bot.py
   ```

## Commands

- `/play <url>` — join your channel and queue a YouTube URL
- `/skip`, `/pause`, `/resume`
- `/queue`, `/stop`, `/leave`

The bot streams media rather than downloading it. Keep `yt-dlp` current, and
use the bot only for material you are permitted to play.
