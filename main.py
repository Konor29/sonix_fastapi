import discord
from discord.ext import commands
import os
import re
import logging
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

SPOTIPY_CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')

from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Song queue per guild
song_queues = {}
# Elevator music enabled per guild

elevator_enabled = {}

def is_elevator_enabled(ctx):
    # Default to True if not set
    return elevator_enabled.get(ctx.guild.id, True)

# Helper to get the queue for a guild
def get_queue(ctx):
    return song_queues.setdefault(ctx.guild.id, [])

# Elevator music fallback implementation
import asyncio

ELEVATOR_MUSIC_PATH = "elevator.mp3"
TTS_DONE_PATH = "done.mp3"

async def play_elevator_music(ctx):
    # Check if elevator music is enabled for this guild
    if not is_elevator_enabled(ctx):
        return
    # Play TTS message first
    if ctx.voice_client:
        source = discord.FFmpegPCMAudio(TTS_DONE_PATH)
        ctx.voice_client.play(source)
        while ctx.voice_client.is_playing():
            await asyncio.sleep(1)
    # Then play elevator music on loop at low volume
    while not get_queue(ctx):
        if not is_elevator_enabled(ctx):
            break
        if ctx.voice_client and not ctx.voice_client.is_playing():
            source = discord.FFmpegPCMAudio(ELEVATOR_MUSIC_PATH)
            source = discord.PCMVolumeTransformer(source, volume=0.05)
            ctx.voice_client.play(source)
        await asyncio.sleep(5)
        # If a new song is queued, break and play the song
        if get_queue(ctx):
            ctx.voice_client.stop()
            break

def play_next(ctx):
    queue = get_queue(ctx)
    # Cancel elevator music if running
    if hasattr(ctx.bot, 'elevator_task') and ctx.bot.elevator_task:
        ctx.bot.elevator_task.cancel()
        ctx.bot.elevator_task = None
    if hasattr(ctx.bot, 'disconnect_task') and ctx.bot.disconnect_task:
        ctx.bot.disconnect_task.cancel()
        ctx.bot.disconnect_task = None
    if queue:
        next_query = queue.pop(0)
        ctx.bot.last_query = next_query
        ctx.bot.loop.create_task(play_next_with_delay(ctx, next_query))
        # Cancel disconnect timer if music starts
        if hasattr(ctx.bot, 'disconnect_task') and ctx.bot.disconnect_task:
            ctx.bot.disconnect_task.cancel()
            ctx.bot.disconnect_task = None
    else:
        # Start elevator music fallback only if enabled
        if is_elevator_enabled(ctx) and not getattr(ctx.bot, 'prevent_fallback', False):
            ctx.bot.elevator_task = ctx.bot.loop.create_task(play_elevator_music(ctx))
        else:
            # Elevator is disabled: start a 5-minute disconnect timer
            ctx.bot.disconnect_task = ctx.bot.loop.create_task(disconnect_after_timeout(ctx, 300))

# Disconnect after timeout if still idle
async def disconnect_after_timeout(ctx, timeout):
    await asyncio.sleep(timeout)
    voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
    if voice and (not get_queue(ctx)) and not is_elevator_enabled(ctx):
        await voice.disconnect()
        logging.getLogger("sonix_playback").info(f"[Sonix] Disconnected from {ctx.guild.name} after 5 minutes of inactivity.")

import asyncio
async def play_next_with_delay(ctx, next_query):
    await asyncio.sleep(1)
    await play_song(ctx, next_query)

# Preload the next song in the queue for instant transitions
def get_next_query(ctx):
    queue = get_queue(ctx)
    if queue:
        return queue[0]
    return None

async def preload_next_song(ctx):
    next_query = get_next_query(ctx)
    if not next_query:
        return
    import re
    is_search = not (isinstance(next_query, str) and re.match(r"https?://", next_query))
    ydl_opts = {
        'format': 'bestaudio',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch' if is_search else None,
        'outtmpl': 'song.%(ext)s',
    }
    # Only preload if not already cached
    if not get_cached_ytdlp(next_query):
        loop = asyncio.get_running_loop()
        try:
            audio_url, title, info = await loop.run_in_executor(
                process_pool, functools.partial(ytdlp_extract, next_query, ydl_opts)
            )
            set_cached_ytdlp(next_query, (audio_url, title, info))
            logging.getLogger("sonix_playback").info(f"[Sonix] Preloaded next song: {title}")
        except Exception as e:
            logging.getLogger("sonix_playback").error(f"[Sonix] Error preloading next song: {e}")

# Command to enable/disable elevator music
@bot.command()
async def elevator(ctx, mode: str = None):
    """Enable or disable elevator music fallback. Usage: !elevator [on/off/status]"""
    gid = ctx.guild.id
    if mode is None or mode.lower() == "status":
        status = "enabled" if is_elevator_enabled(ctx) else "disabled"
        await ctx.send(f"Elevator music is currently **{status}** for this server.")
    elif mode.lower() in ["on", "enable"]:
        elevator_enabled[gid] = True
        await ctx.send("Elevator music **enabled** for this server!")
    elif mode.lower() in ["off", "disable"]:
        elevator_enabled[gid] = False
        await ctx.send("Elevator music **disabled** for this server!")
        # Stop elevator music if currently playing
        if hasattr(ctx.bot, 'elevator_task') and ctx.bot.elevator_task:
            ctx.bot.elevator_task.cancel()
            ctx.bot.elevator_task = None
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
    else:
        await ctx.send("Usage: !elevator [on/off/status]")

# Custom help command for Sonix
@bot.command(aliases=["m!help", "m!h", "?help", "?h"])
async def sonixhelp(ctx):
    embed = discord.Embed(title="Sonix Music Bot Help", color=discord.Color.blue())
    embed.description = (
        "**Prefix:** `!` or `m!` or `?`\n"
        "Here are the available commands and their aliases:"
    )
    embed.add_field(name="▶️ Play", value="`!play <song/url>` or `!p` or `m!p` — Play a song or add to the queue.", inline=False)
    embed.add_field(name="📄 Queue", value="`!queue` or `!q` or `m!q` — Show the current song queue.", inline=False)
    embed.add_field(name="⏭️ Skip", value="`!skip` or `!s` or `m!s` — Skip the current song.", inline=False)
    embed.add_field(name="⏸️ Pause", value="`!pause` or `!pa` or `m!pa` — Pause playback.", inline=False)
    embed.add_field(name="▶️ Resume", value="`!resume` or `!r` or `m!r` — Resume playback.", inline=False)
    embed.add_field(name="⏹️ Stop", value="`!stop` or `!st` or `m!st` — Stop playback and clear the queue.", inline=False)
    embed.add_field(name="🔊 Join", value="`!join` or `!j` or `m!j` — Make the bot join your voice channel.", inline=False)
    embed.add_field(name="🎵 Elevator Music", value="`!elevator [on/off/status]` — Enable or disable elevator music fallback.", inline=False)
    embed.add_field(name="❓ Help", value="`!sonixhelp` or `!m!help` or `!m!h` or `?help` or `?h` — Show this help message.", inline=False)
    embed.set_footer(text="Thank you for using Sonix! Need help? Contact support@sonixbot.com")
    await ctx.send(embed=embed)

# Helper to play a song (used for both play and next)
import functools
import concurrent.futures
from collections import OrderedDict

# Global process pool for yt-dlp
process_pool = concurrent.futures.ProcessPoolExecutor(max_workers=4)

# Simple LRU cache for yt-dlp results (max 128 unique queries)
ytdlp_cache = OrderedDict()
YTDLP_CACHE_SIZE = 128

def get_cached_ytdlp(query):
    if query in ytdlp_cache:
        ytdlp_cache.move_to_end(query)
        return ytdlp_cache[query]
    return None

def set_cached_ytdlp(query, value):
    ytdlp_cache[query] = value
    ytdlp_cache.move_to_end(query)
    if len(ytdlp_cache) > YTDLP_CACHE_SIZE:
        ytdlp_cache.popitem(last=False)

# Move ytdlp_extract to module level so it can be pickled for ProcessPoolExecutor

def ytdlp_extract(query, ydl_opts):
    from yt_dlp import YoutubeDL
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(query, download=False)
        if 'entries' in info:
            info = info['entries'][0]
        audio_url = info['url']
        title = info.get('title', 'Unknown Title')
        return audio_url, title, info

async def play_song(ctx, query, retry_count=0):
    # Cancel elevator music if running
    if hasattr(ctx.bot, 'elevator_task') and ctx.bot.elevator_task:
        ctx.bot.elevator_task.cancel()
        ctx.bot.elevator_task = None
    import re
    logger = logging.getLogger("sonix_playback")
    is_ytmusic = isinstance(query, str) and re.match(r"https?://music\.youtube\.com/", query)
    is_search = not (isinstance(query, str) and re.match(r"https?://", query))
    ydl_opts = {
        'format': 'bestaudio',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch' if is_search else None,
        'outtmpl': 'song.%(ext)s',
    }

    # Check cache first
    cached = get_cached_ytdlp(query)
    if cached:
        audio_url, title, info = cached
        logger.info(f"[Sonix] [CACHE] Successfully extracted: {title}")
    else:
        loop = asyncio.get_running_loop()
        try:
            audio_url, title, info = await loop.run_in_executor(
                process_pool, functools.partial(ytdlp_extract, query, ydl_opts)
            )
            set_cached_ytdlp(query, (audio_url, title, info))
            logger.info(f"[Sonix] Successfully extracted: {title}")
        except Exception as e:
            logger.error(f"[Sonix] Error extracting info: {e}")
            if retry_count < 1:
                logger.info(f"[Sonix] Retrying extraction for: {query}")
                await play_song(ctx, query, retry_count=retry_count+1)
                return
            embed = discord.Embed(title="❌ Error", description=f"Could not play the requested song after retry.\n```{str(e)}```", color=discord.Color.red())
            await ctx.send(embed=embed)
            return

    try:
        logger.info(f"[Sonix] Starting playback: {title}")
        source = await discord.FFmpegOpusAudio.from_probe(
            audio_url,
            options='-analyzeduration 0 -probesize 32'
        )
        # Preload the next song in the queue
        ctx.bot.loop.create_task(preload_next_song(ctx))
        async def handle_after_playing_error(err):
            channel = ctx.channel
            logger.error(f"[Sonix] Playback error: {err}")
            embed = discord.Embed(title="❌ Playback Error", description=f"There was an error during playback: ```{err}```\nSkipping to the next song.", color=discord.Color.red())
            await channel.send(embed=embed)

        def after_playing(err):
            if err:
                logger.error(f"[Sonix] Error in after_playing: {err}")
                ctx.bot.loop.create_task(handle_after_playing_error(err))
            else:
                logger.info(f"[Sonix] Song finished: {title}")
            ctx.bot.loop.call_soon_threadsafe(play_next, ctx)

        ctx.voice_client.play(source, after=after_playing)
        if is_ytmusic or (is_search and 'music.youtube.com' in info.get('webpage_url', '')):
            embed = discord.Embed(title="🎶 Now Playing from YouTube Music", description=f"**[{title}]({info.get('webpage_url', '')})**", color=discord.Color.red())
        else:
            embed = discord.Embed(title="🎶 Now Playing", description=f"**[{title}]({info.get('webpage_url', '')})**", color=discord.Color.green())
        if 'thumbnail' in info:
            embed.set_thumbnail(url=info['thumbnail'])
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"[Sonix] Error starting playback: {e}")
        if retry_count < 1:
            logger.info(f"[Sonix] Retrying playback for: {title}")
            await play_song(ctx, query, retry_count=retry_count+1)
            return
        embed = discord.Embed(title="❌ Error", description=f"Could not start playback after retry.\n```{str(e)}```", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    logging.basicConfig(level=logging.INFO)

# This is where music commands will go

@bot.command(aliases=["m!j", "j"])
async def join(ctx):
    """Joins the voice channel you are in."""
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        await channel.connect()
        embed = discord.Embed(title="🔊 Joined Voice Channel", description=f"Joined **{channel}**!", color=discord.Color.blurple())
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(title="❌ Error", description="You are not in a voice channel.", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command(aliases=["m!p", "p"])
async def play(ctx, *, query):
    """Adds a song or Spotify track/album/playlist to the queue or plays if nothing is playing."""
    queue = get_queue(ctx)
    # Check if query is a Spotify link
    spotify_pattern = r"https://open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
    match = re.match(spotify_pattern, query)
    # Determine if the bot is already playing or paused
    is_playing = ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused())

    if match:
        # Set up Spotify API
        client_id = os.getenv('SPOTIPY_CLIENT_ID')
        client_secret = os.getenv('SPOTIPY_CLIENT_SECRET')
        if not client_id or not client_secret:
            embed = discord.Embed(title="❌ Spotify Error", description="Spotify support requires SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET as environment variables.", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials())
        link_type, spotify_id = match.groups()
        try:
            if link_type == 'track':
                track = sp.track(spotify_id)
                search_query = f"{track['name']} {track['artists'][0]['name']}"
                queue.append(search_query)
                if is_playing:
                    embed = discord.Embed(title="🟢 Added from Spotify", description=f"**{search_query}**", color=discord.Color.green())
                    await ctx.send(embed=embed)
            elif link_type == 'album':
                album = sp.album(spotify_id)
                tracks = album['tracks']['items']
                for t in tracks:
                    search_query = f"{t['name']} {t['artists'][0]['name']}"
                    queue.append(search_query)
                if is_playing:
                    embed = discord.Embed(title="🟢 Added Album from Spotify", description=f"Added **{len(tracks)}** tracks from album!", color=discord.Color.green())
                    await ctx.send(embed=embed)
            elif link_type == 'playlist':
                playlist = sp.playlist(spotify_id)
                tracks = playlist['tracks']['items']
                count = 0
                for item in tracks:
                    t = item['track']
                    if t is not None:
                        search_query = f"{t['name']} {t['artists'][0]['name']}"
                        queue.append(search_query)
                        count += 1
                if is_playing:
                    embed = discord.Embed(title="🟢 Added Playlist from Spotify", description=f"Added **{count}** tracks from playlist!", color=discord.Color.green())
                    await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(title="❌ Spotify Error", description=f"Error processing Spotify link.\n```{str(e)}```", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
    else:
        queue.append(query)
        # Detect if query is a link (URL)
        is_url = isinstance(query, str) and re.match(r"https?://", query)
        is_ytmusic = isinstance(query, str) and re.match(r"https?://music\.youtube\.com/", query)
        is_search = not is_url
        if is_playing:
            if is_url:
                from yt_dlp import YoutubeDL
                ydl_opts = {
                    'format': 'bestaudio',
                    'noplaylist': True,
                    'quiet': True,
                }
                async def fetch_metadata():
                    try:
                        def ytdlp_extract():
                            with YoutubeDL(ydl_opts) as ydl:
                                info = ydl.extract_info(query, download=False)
                                if 'entries' in info:
                                    info = info['entries'][0]
                                return info
                        info = await asyncio.to_thread(ytdlp_extract)
                        title = info.get('title', query)
                        url = info.get('webpage_url', query)
                        embed = discord.Embed(title="➕ Added to Queue", description=f"**[{title}]({url})**", color=discord.Color.blurple())
                        if 'thumbnail' in info:
                            embed.set_thumbnail(url=info['thumbnail'])
                        await ctx.send(embed=embed)
                    except Exception as e:
                        embed = discord.Embed(title="➕ Added to Queue", description=f"**{query}**", color=discord.Color.blurple())
                        await ctx.send(embed=embed)
                await fetch_metadata()
            elif is_ytmusic or (is_search and 'music.youtube.com' in query):
                embed = discord.Embed(title="➕ Added from YouTube Music", description=f"**{query}**", color=discord.Color.red())
                await ctx.send(embed=embed)
            else:
                embed = discord.Embed(title="➕ Added to Queue", description=f"**{query}**", color=discord.Color.blurple())
                await ctx.send(embed=embed)
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("You are not in a voice channel.")
                return
        play_next(ctx)

@bot.command(aliases=["m!pa", "pa"])
async def pause(ctx):
    """Pauses the current song."""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        embed = discord.Embed(title="⏸️ Paused", description="Paused the music.", color=discord.Color.blurple())
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(title="❌ Error", description="Nothing is playing.", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command(aliases=["m!r", "r"])
async def resume(ctx):
    """Resumes the paused song."""
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        embed = discord.Embed(title="▶️ Resumed", description="Resumed the music.", color=discord.Color.blurple())
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(title="❌ Error", description="Nothing is paused.", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command(aliases=["m!s", "s"])
async def skip(ctx):
    """Skips the current song and plays the next in queue."""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("Skipped the song.")
    else:
        await ctx.send("Nothing is playing to skip.")

@bot.command()
async def replay(ctx):
    """Replays the last played song (adds it to the front of the queue)."""
    if not hasattr(ctx.bot, 'last_query') or not ctx.bot.last_query:
        await ctx.send("No song to replay.")
        return
    queue = get_queue(ctx)
    queue.insert(0, ctx.bot.last_query)
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        play_next(ctx)
    await ctx.send("Replaying last song!")

@bot.command(aliases=["m!st", "st"])
async def stop(ctx):
    """Stops the music, clears the queue, and leaves the voice channel."""
    song_queues[ctx.guild.id] = []
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        embed = discord.Embed(title="🛑 Stopped & Left", description="Stopped the music, cleared the queue, and left the channel.", color=discord.Color.orange())
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(title="❌ Error", description="I'm not in a voice channel.", color=discord.Color.red())
        await ctx.send(embed=embed)

# Save last played query for replay
@bot.listen('on_command')
async def save_last_query(ctx):
    if ctx.command and ctx.command.name == 'play':
        ctx.bot.last_query = ctx.kwargs.get('query')

@bot.command(aliases=["m!q", "q"])
async def queue(ctx):
    """Shows the current song queue."""
    queue = get_queue(ctx)
    if not queue:
        embed = discord.Embed(title="🎶 Song Queue", description="The queue is empty.", color=discord.Color.blurple())
        await ctx.send(embed=embed)
    else:
        async def get_metadata(q):
            # Use the same process pool and cache as playback
            cached = get_cached_ytdlp(q)
            if cached:
                _, title, info = cached
            else:
                ydl_opts = {
                    'format': 'bestaudio',
                    'noplaylist': True,
                    'quiet': True,
                    'outtmpl': 'song.%(ext)s',
                }
                loop = asyncio.get_running_loop()
                try:
                    audio_url, title, info = await loop.run_in_executor(
                        process_pool, functools.partial(ytdlp_extract, q, ydl_opts)
                    )
                    set_cached_ytdlp(q, (audio_url, title, info))
                except Exception:
                    return q, q, None
            title = info.get('title', q)
            url = info.get('webpage_url', q)
            thumbnail = info.get('thumbnail')
            return title, url, thumbnail
        fields = []
        for i, q in enumerate(queue[:10]):
            # Only fetch metadata for real URLs (http/https), never for search queries or ytsearchmusic: etc
            if (
                isinstance(q, str)
                and q.startswith("http")
                and not q.startswith("ytsearchmusic:")
                and not q.startswith("ytsearch:")
            ):
                try:
                    title, url, thumbnail = await get_metadata(q)
                    fields.append((f"{i+1}.", f"[{title}]({url})", thumbnail))
                except Exception as e:
                    logging.getLogger("sonix_playback").error(f"Error fetching queue metadata: {e}")
                    fields.append((f"{i+1}.", q, None))
            else:
                # Always treat non-URLs and search queries as plain text
                fields.append((f"{i+1}.", q, None))
        for idx, (name, value, thumbnail) in enumerate(fields):
            embed.add_field(name=name, value=value, inline=False)
            if idx == 0 and thumbnail:
                embed.set_thumbnail(url=thumbnail)
        if len(queue) > max_display:
            embed.set_footer(text=f"...and {len(queue) - max_display} more in the queue!")
        else:
            embed.set_footer(text=f"Total songs in queue: {len(queue)}")
        await ctx.send(embed=embed)

# The official help command is now !sonixhelp (with aliases), see above.
# All blocking yt-dlp calls in queue and play_song use asyncio.to_thread to avoid lag.

# --- Track Last Command Channel ---

@bot.before_invoke
async def set_last_text_channel(ctx):
    if ctx.guild:
        setattr(bot, f'last_text_channel_{ctx.guild.id}', ctx.channel)

# --- Voice State and Disconnect Handlers ---

@bot.event
async def on_voice_state_update(member, before, after):
    # Only act if this is the bot itself
    if member.id != bot.user.id:
        return
    # Find the last used text channel for this guild
    channel = getattr(bot, f'last_text_channel_{member.guild.id}', None)
    # 1. Pause music if server muted (not deafened), unpause if unmuted
    voice = discord.utils.get(bot.voice_clients, guild=member.guild)
    # If server muted (not deafened), pause
    if (after.self_mute or after.mute) and after.channel is not None:
        if voice and voice.is_playing():
            voice.pause()
        if channel:
            embed = discord.Embed(
                title="🔇 Server Muted",
                description="I have been server muted and paused playback. Unmute me to resume music.",
                color=discord.Color.orange()
            )
            await channel.send(embed=embed)
    # If unmuted (was previously muted), unpause
    elif (before.self_mute or before.mute) and not (after.self_mute or after.mute):
        if voice and voice.is_paused():
            voice.resume()
        if channel:
            embed = discord.Embed(
                title="🔊 Server Unmuted",
                description="I am no longer server muted and have resumed playback.",
                color=discord.Color.green()
            )
            await channel.send(embed=embed)
    # Do nothing on deafened/undeafened
    # 2. On disconnect, clear queue and prevent fallback music
    if before.channel and not after.channel:
        # Clear the queue for this guild
        if hasattr(bot, 'queues') and member.guild.id in bot.queues:
            bot.queues[member.guild.id].clear()
        # Cancel any elevator/done/disconnect tasks
        if hasattr(bot, 'elevator_task') and bot.elevator_task:
            bot.elevator_task.cancel()
            bot.elevator_task = None
        if hasattr(bot, 'disconnect_task') and bot.disconnect_task:
            bot.disconnect_task.cancel()
            bot.disconnect_task = None
        # Set a flag to prevent fallback music
        bot.prevent_fallback = True
        if channel:
            embed = discord.Embed(
                title="👋 Disconnected from Voice",
                description="I have been disconnected from voice and cleared the queue.",
                color=discord.Color.red()
            )
            await channel.send(embed=embed)
    else:
        bot.prevent_fallback = False

# In your play_next and disconnect logic, check bot.prevent_fallback before playing elevator/done music
# Example modification for play_next:
# if is_elevator_enabled(ctx) and not getattr(ctx.bot, 'prevent_fallback', False):
#    ctx.bot.elevator_task = ctx.bot.loop.create_task(play_elevator_music(ctx))

if __name__ == '__main__':
    bot.run(TOKEN)