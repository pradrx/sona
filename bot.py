"""A small Discord music bot that streams audio resolved by yt-dlp."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("sona")

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
}
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"


@dataclass(slots=True)
class Track:
    title: str
    stream_url: str
    webpage_url: str
    requester: str


@dataclass
class GuildPlayer:
    queue: Deque[Track] = field(default_factory=deque)
    current: Track | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


async def extract_track(url: str, requester: str) -> Track:
    """Resolve a playable stream URL without blocking Discord's event loop."""

    def extract() -> Track:
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            info = ydl.extract_info(url, download=False)
            if "entries" in info:
                info = next(entry for entry in info["entries"] if entry)

        return Track(
            title=info.get("title", "Unknown title"),
            stream_url=info["url"],
            webpage_url=info.get("webpage_url", url),
            requester=requester,
        )

    return await asyncio.to_thread(extract)


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.players: dict[int, GuildPlayer] = defaultdict(GuildPlayer)
        self.loop: asyncio.AbstractEventLoop | None = None

    async def _voice_for(self, interaction: discord.Interaction) -> discord.VoiceClient:
        if interaction.guild is None:
            raise app_commands.CheckFailure("This command can only be used in a server.")

        member = interaction.user
        if not isinstance(member, discord.Member) or member.voice is None:
            raise app_commands.CheckFailure("Join a voice channel first.")

        voice = interaction.guild.voice_client
        if voice is None:
            voice = await member.voice.channel.connect()
        elif voice.channel != member.voice.channel:
            await voice.move_to(member.voice.channel)
        return voice

    async def _play_next(self, guild_id: int) -> None:
        """Start the next queued track. This is also called from FFmpeg's callback."""
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        player = self.players[guild_id]
        async with player.lock:
            voice = guild.voice_client
            if voice is None or voice.is_playing() or voice.is_paused():
                return

            if not player.queue:
                player.current = None
                return

            track = player.queue.popleft()
            player.current = track

            try:
                source = discord.FFmpegOpusAudio(
                    track.stream_url,
                    before_options=FFMPEG_BEFORE_OPTIONS,
                    options=FFMPEG_OPTIONS,
                )
                voice.play(source, after=self._after_track(guild_id))
            except Exception:
                player.current = None
                LOGGER.exception("Unable to start playback in guild %s", guild_id)

    def _after_track(self, guild_id: int):
        def after(error: Exception | None) -> None:
            if error:
                LOGGER.error("Playback error in guild %s: %s", guild_id, error)
            if self.loop is not None and not self.loop.is_closed():
                future = asyncio.run_coroutine_threadsafe(self._play_next(guild_id), self.loop)
                future.add_done_callback(self._log_background_error)

        return after

    @staticmethod
    def _log_background_error(future) -> None:
        try:
            future.result()
        except Exception:
            LOGGER.exception("Unable to continue playback")

    @app_commands.command(name="play", description="Queue audio from a YouTube URL")
    @app_commands.describe(url="A YouTube video URL")
    async def play(self, interaction: discord.Interaction, url: str) -> None:
        self.loop = asyncio.get_running_loop()
        await interaction.response.defer(thinking=True)

        try:
            await self._voice_for(interaction)
            track = await extract_track(url, interaction.user.display_name)
        except app_commands.CheckFailure as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unable to extract URL: %s", url)
            await interaction.followup.send(
                "I couldn't get playable audio from that URL. Try another public video."
            )
            return

        assert interaction.guild is not None
        player = self.players[interaction.guild.id]
        player.queue.append(track)
        await self._play_next(interaction.guild.id)

        position = len(player.queue) + (1 if player.current else 0)
        message = f"Queued **{track.title}**"
        if position > 1:
            message += f" (position {position})"
        await interaction.followup.send(message)

    @app_commands.command(name="skip", description="Skip the current track")
    async def skip(self, interaction: discord.Interaction) -> None:
        try:
            voice = await self._voice_for(interaction)
        except app_commands.CheckFailure as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return

        if not voice.is_playing() and not voice.is_paused():
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        voice.stop()
        await interaction.response.send_message("Skipped.")

    @app_commands.command(name="pause", description="Pause the current track")
    async def pause(self, interaction: discord.Interaction) -> None:
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice is None or not voice.is_playing():
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        voice.pause()
        await interaction.response.send_message("Paused.")

    @app_commands.command(name="resume", description="Resume the current track")
    async def resume(self, interaction: discord.Interaction) -> None:
        voice = interaction.guild.voice_client if interaction.guild else None
        if voice is None or not voice.is_paused():
            await interaction.response.send_message("Nothing is paused.", ephemeral=True)
            return
        voice.resume()
        await interaction.response.send_message("Resumed.")

    @app_commands.command(name="queue", description="Show the queued tracks")
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        player = self.players[interaction.guild.id]
        lines = []
        if player.current:
            lines.append(f"Now playing: **{player.current.title}**")
        lines.extend(f"{index}. {track.title}" for index, track in enumerate(player.queue, start=1))
        await interaction.response.send_message("\n".join(lines) if lines else "The queue is empty.")

    @app_commands.command(name="stop", description="Stop playback and clear the queue")
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        player = self.players[interaction.guild.id]
        player.queue.clear()
        player.current = None
        voice = interaction.guild.voice_client
        if voice and (voice.is_playing() or voice.is_paused()):
            voice.stop()
        await interaction.response.send_message("Stopped and cleared the queue.")

    @app_commands.command(name="leave", description="Leave voice and clear the queue")
    async def leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.guild.voice_client is None:
            await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
            return
        self.players.pop(interaction.guild.id, None)
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Disconnected and cleared the queue.")


class SonaBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.add_cog(Music(self))
        await self.tree.sync()


def main() -> None:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token or token == "replace-me":
        raise RuntimeError("Set DISCORD_TOKEN in your .env file before starting the bot.")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "FFmpeg is required but was not found on PATH. Install it, then try again."
        )

    intents = discord.Intents.default()
    bot = SonaBot(command_prefix="!", intents=intents)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
