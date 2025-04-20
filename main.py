import discord
from discord.ext import commands
import os
import re
import logging
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import json
import secrets

SPOTIPY_CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')

from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Persistent per-server keys
guild_keys_path = 'guild_keys.json'
if os.path.exists(guild_keys_path):
    with open(guild_keys_path, 'r') as f:
        guild_keys = json.load(f)
else:
    guild_keys = {}

def save_guild_keys():
    with open(guild_keys_path, 'w') as f:
        json.dump(guild_keys, f)

# Ensure every guild has a key
def ensure_guild_key(guild_id):
    gid = str(guild_id)
    if gid not in guild_keys:
        guild_keys[gid] = secrets.token_urlsafe(16)
        save_guild_keys()
    return guild_keys[gid]

@bot.event
async def on_guild_join(guild):
    ensure_guild_key(guild.id)

@bot.command()
async def getkey(ctx):
    key = ensure_guild_key(ctx.guild.id)
    guild_id = ctx.guild.id
    try:
        await ctx.author.send(f"Your server's control key is: `{key}`\nGuild ID: `{guild_id}`\nKeep this secret!")
        await ctx.reply("I've sent you the server key and guild ID in a DM!", mention_author=False)
    except discord.Forbidden:
        await ctx.reply(f"Your server's control key is: `{key}`\nGuild ID: `{guild_id}`\n(Enable DMs to receive this privately)", mention_author=False)

# Song queue per guild (list of dicts with metadata)
song_queues = {}  # {guild_id: [song_dict, ...]}
# Track now playing and last played per guild
now_playing = {}  # {guild_id: song_dict}
last_played = {}  # {guild_id: song_dict}
# Elevator music enabled per guild
elevator_enabled = {}

def is_elevator_enabled(ctx):
    # Default to True if not set
    return elevator_enabled.get(ctx.guild.id, True)

# Helper to get the queue for a guild
def get_queue(ctx):
    return song_queues.setdefault(ctx.guild.id, [])

def add_to_queue(ctx, song):
    # song is a dict with metadata
    queue = get_queue(ctx)
    queue.append(song)

# Elevator music fallback implementation
import asyncio

ELEVATOR_MUSIC_PATH = "elevator.mp3"
TTS_DONE_PATH = "done.mp3"

async def play_elevator_music(ctx):
    # Only play elevator music if enabled and nothing else is playing
    if not is_elevator_enabled(ctx):
        return
    # Play TTS message first (optional, can remove if not needed)
    if ctx.voice_client and not ctx.voice_client.is_playing():
        source = discord.FFmpegPCMAudio(TTS_DONE_PATH)
        ctx.voice_client.play(source)
        while ctx.voice_client.is_playing():
            await asyncio.sleep(1)
    # Play elevator music on loop at low volume ONLY if queue is empty and nothing is playing
    while True:
        if not is_elevator_enabled(ctx):
            break
        # If a song is queued, stop elevator music immediately
        if get_queue(ctx):
            if ctx.voice_client and ctx.voice_client.is_playing():
                ctx.voice_client.stop()
            break
        # If nothing is playing, play elevator music
        if ctx.voice_client and not ctx.voice_client.is_playing():
            source = discord.FFmpegPCMAudio(ELEVATOR_MUSIC_PATH)
            source = discord.PCMVolumeTransformer(source, volume=0.05)
            ctx.voice_client.play(source)
        await asyncio.sleep(3)
        # If a song is queued, stop elevator music immediately
        if get_queue(ctx):
            if ctx.voice_client and ctx.voice_client.is_playing():
                ctx.voice_client.stop()
            break


def play_next(ctx):
    # Use a per-guild playback flag to prevent double starts
    if not hasattr(ctx.bot, 'is_playing_flag'):
        ctx.bot.is_playing_flag = {}
    flag = ctx.bot.is_playing_flag.setdefault(ctx.guild.id, False)
    if flag:
        # Already playing, don't trigger again
        return
    queue = get_queue(ctx)
    # Cancel elevator music if running
    if hasattr(ctx.bot, 'elevator_task') and ctx.bot.elevator_task:
        ctx.bot.elevator_task.cancel()
        ctx.bot.elevator_task = None
    if hasattr(ctx.bot, 'disconnect_task') and ctx.bot.disconnect_task:
        ctx.bot.disconnect_task.cancel()
        ctx.bot.disconnect_task = None
    if queue:
        ctx.bot.is_playing_flag[ctx.guild.id] = True
        ctx.bot.loop.create_task(play_next_with_delay(ctx))
        # Cancel disconnect timer if music starts
        if hasattr(ctx.bot, 'disconnect_task') and ctx.bot.disconnect_task:
            ctx.bot.disconnect_task.cancel()
            ctx.bot.disconnect_task = None
    else:
        now_playing[ctx.guild.id] = None
        ctx.bot.is_playing_flag[ctx.guild.id] = False
        # Start elevator music fallback only if enabled
        if is_elevator_enabled(ctx) and not getattr(ctx.bot, 'prevent_fallback', False):
            ctx.bot.elevator_task = ctx.bot.loop.create_task(play_elevator_music(ctx))
        else:
            ctx.bot.disconnect_task = ctx.bot.loop.create_task(disconnect_after_timeout(ctx, 300))

# Disconnect after timeout if still idle
async def disconnect_after_timeout(ctx, timeout):
    await asyncio.sleep(timeout)
    voice = discord.utils.get(ctx.bot.voice_clients, guild=ctx.guild)
    if voice and (not get_queue(ctx)) and not is_elevator_enabled(ctx):
        await voice.disconnect()
        logging.getLogger("sonix_playback").info(f"[Sonix] Disconnected from {ctx.guild.name} after 5 minutes of inactivity.")

import asyncio
async def play_next_with_delay(ctx):
    await asyncio.sleep(1)
    queue = get_queue(ctx)
    if queue:
        next_song = queue.pop(0)
        # Track now playing and last played
        last_played[ctx.guild.id] = now_playing.get(ctx.guild.id)
        now_playing[ctx.guild.id] = next_song
        await play_song(ctx, next_song)
    else:
        now_playing[ctx.guild.id] = None
    # Don't clear flag here; only after playback is truly finished


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
        result = await loop.run_in_executor(
            process_pool, functools.partial(ytdlp_extract, next_query, ydl_opts)
        )
        if result:
            audio_url, title, info = result
            set_cached_ytdlp(next_query, (audio_url, title, info))
            logging.getLogger("sonix_playback").info(f"[Sonix] Preloaded next song: {title}")
        else:
            logging.getLogger("sonix_playback").error(f"[Sonix] Error preloading next song: {next_query}")

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
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            audio_url = info.get('url')
            title = info.get('title', query)
            return audio_url, title, info
    except Exception:
        return None

async def fetch_song_metadata(q):
    from yt_dlp import YoutubeDL
    import re
    import logging
    # If not a URL, prefix with ytsearch:
    if not (isinstance(q, str) and re.match(r"https?://", q)):
        q = f"ytsearch:{q}"
    logger = logging.getLogger("sonix_debug")
    # Print the exact query being sent to yt-dlp
    try:
        import inspect
        calling_ctx = None
        for frame in inspect.stack():
            if 'ctx' in frame.frame.f_locals:
                calling_ctx = frame.frame.f_locals['ctx']
                break
        if calling_ctx:
            await calling_ctx.send(f"[DEBUG] fetch_song_metadata is sending query to yt-dlp: {q}")
        logger.info(f"[DEBUG] fetch_song_metadata is sending query to yt-dlp: {q}")
    except Exception as e:
        logger.error(f"[DEBUG] Could not send debug message for query: {e}")
    ydl_opts = {
        'format': 'bestaudio',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch',
        'cookiefile': 'youtube_cookies.txt',
    }
    def ytdlp_extract():
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(q, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                return info
        except Exception as e:
            logger.error(f"[DEBUG] yt-dlp exception: {e}")
            try:
                if calling_ctx:
                    import asyncio
                    asyncio.run(calling_ctx.send(f"[DEBUG] yt-dlp exception: {e}"))
            except Exception:
                pass
            return None
    info = await asyncio.to_thread(ytdlp_extract)
    if not info:
        return None
    return {
        'title': info.get('title', q),
        'webpage_url': info.get('webpage_url', q),
        'thumbnail': info.get('thumbnail', ''),
        'audio_url': info.get('url', ''),
        'info': info,
        'query': q,
    }

async def fetch_multiple_song_metadata(queries):
    # Helper to fetch metadata for a list of queries in parallel
    results = await asyncio.gather(*(fetch_song_metadata(q) for q in queries))
    # Filter out None (failed) results
    return [song for song in results if song is not None]

async def expand_spotify_url_to_queries(spotify_url):
    import re
    import os
    import spotipy
    from spotipy.oauth2 import SpotifyClientCredentials
    spotify_pattern = r"https://open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
    match = re.match(spotify_pattern, spotify_url)
    if not match:
        return None
    client_id = os.getenv('SPOTIPY_CLIENT_ID')
    client_secret = os.getenv('SPOTIPY_CLIENT_SECRET')
    if not client_id or not client_secret:
        return None
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials())
    link_type, spotify_id = match.groups()
    queries = []
    try:
        if link_type == 'track':
            track = sp.track(spotify_id)
            queries = [f"{track['name']} {track['artists'][0]['name']}"]
        elif link_type == 'album':
            album = sp.album(spotify_id)
            tracks = album['tracks']['items']
            queries = [f"{t['name']} {t['artists'][0]['name']}" for t in tracks]
        elif link_type == 'playlist':
            playlist = sp.playlist(spotify_id)
            tracks = playlist['tracks']['items']
            for item in tracks:
                t = item['track']
                if t is not None:
                    queries.append(f"{t['name']} {t['artists'][0]['name']}")
    except Exception:
        return None
    # Fetch YouTube metadata for all queries
    if not queries:
        return None
    songs = await fetch_multiple_song_metadata(queries)
    return songs if songs else None

async def play_song(ctx, query_or_song, retry_count=0):
    # Cancel elevator music if running
    if hasattr(ctx.bot, 'elevator_task') and ctx.bot.elevator_task:
        ctx.bot.elevator_task.cancel()
        ctx.bot.elevator_task = None
    import re
    logger = logging.getLogger("sonix_playback")
    # If passed a dict (already extracted), use it directly
    if isinstance(query_or_song, dict):
        song = query_or_song
        audio_url = song['audio_url']
        title = song['title']
        info = song['info']
        is_ytmusic = song.get('is_ytmusic', False)
        is_search = song.get('is_search', False)
        query = song.get('query', None)
    else:
        # If passed a Spotify URL, expand to YouTube search queries
        spotify_pattern = r"https://open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
        match = re.match(spotify_pattern, str(query_or_song))
        if match:
            await ctx.send("🔎 Expanding Spotify link and searching YouTube for playable tracks. This may take a moment for large playlists...")
            expanded = await expand_spotify_url_to_queries(str(query_or_song))
            if not expanded:
                await ctx.send("❌ Could not extract any playable tracks from this Spotify link.")
                return
            first, *rest = expanded
            await ctx.send(f"▶️ Now playing: **{first['title']}** (from Spotify)")
            await play_song(ctx, first)
            queue = get_queue(ctx)
            # Incrementally queue the rest in the background
            async def queue_spotify_tracks(rest_tracks):
                added = 0
                for song in rest_tracks:
                    queue.append(song)
                    added += 1
                    if added % 5 == 0 or added == len(rest_tracks):
                        await ctx.send(f"➕ Added {added}/{len(rest_tracks)} tracks from the Spotify playlist to the queue.")
                await ctx.send(f"✅ Finished adding {len(rest_tracks)} tracks from Spotify playlist to the queue!")
            ctx.bot.loop.create_task(queue_spotify_tracks(rest))
            return
        query = query_or_song
        is_ytmusic = isinstance(query, str) and re.match(r"https?://music\.youtube\.com/", query)
        is_search = not (isinstance(query, str) and re.match(r"https?://", query))
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'noplaylist': True,
            'default_search': 'ytsearch' if is_search else None,
            'nocheckcertificate': True,
            'extract_flat': 'in_playlist',
            'cookiefile': 'youtube_cookies.txt',
            'outtmpl': '%(title)s.%(ext)s',
            'cachedir': False,
            'source_address': '0.0.0.0',  # Bind to IPv4
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'DNT': '1',
                'Upgrade-Insecure-Requests': '1',
            },
        }
        # Check cache first
        cached = get_cached_ytdlp(query)
        if cached:
            audio_url, title, info = cached
            logger.info(f"[Sonix] [CACHE] Successfully extracted: {title}")
        else:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                process_pool, functools.partial(ytdlp_extract, query, ydl_opts)
            )
            if result:
                audio_url, title, info = result
                set_cached_ytdlp(query, (audio_url, title, info))
                logger.info(f"[Sonix] Successfully extracted: {title}")
            else:
                logger.error(f"[Sonix] Error extracting info: {query}")
                if retry_count < 1:
                    logger.info(f"[Sonix] Retrying extraction for: {query}")
                    await play_song(ctx, query, retry_count=retry_count+1)
                    return
                embed = discord.Embed(title="❌ Error", description=f"Could not play the requested song after retry.", color=discord.Color.red())
                await ctx.send(embed=embed)
                return
        # Build the song dict
        song = {
            'audio_url': audio_url,
            'title': title,
            'info': info,
            'webpage_url': info.get('webpage_url', ''),
            'thumbnail': info.get('thumbnail', ''),
            'is_ytmusic': is_ytmusic,
            'is_search': is_search,
            'query': query,
        }

    # If voice client is already playing, do not attempt playback or send error
    voice = ctx.voice_client
    if voice and voice.is_playing():
        logger.info(f"[Sonix] play_song called but audio is already playing. Suppressing error.")
        return
    try:
        logger.info(f"[Sonix] Starting playback: {song['title']}")
        source = await discord.FFmpegOpusAudio.from_probe(
            song['audio_url'],
            options='-analyzeduration 0 -probesize 32'
        )
        # Preload the next song in the queue
        ctx.bot.loop.create_task(preload_next_song(ctx))
        # Track now playing and last played
        last_played[ctx.guild.id] = now_playing.get(ctx.guild.id)
        now_playing[ctx.guild.id] = song
        async def handle_after_playing_error(err):
            channel = ctx.channel
            logger.error(f"[Sonix] Playback error: {err}")
            embed = discord.Embed(title="❌ Playback Error", description=f"There was an error during playback: ```{err}```\nSkipping to the next song.", color=discord.Color.red())
            await channel.send(embed=embed)
        def after_playing(err):
            # Always clear the playback flag before triggering next
            if hasattr(ctx.bot, 'is_playing_flag'):
                ctx.bot.is_playing_flag[ctx.guild.id] = False
            if err:
                logger.error(f"[Sonix] Error in after_playing: {err}")
                ctx.bot.loop.create_task(handle_after_playing_error(err))
            else:
                logger.info(f"[Sonix] Song finished: {song['title']}")
            ctx.bot.loop.call_soon_threadsafe(play_next, ctx)

        ctx.voice_client.play(source, after=after_playing)
        if song['is_ytmusic'] or (song['is_search'] and 'music.youtube.com' in song['info'].get('webpage_url', '')):
            embed = discord.Embed(title="🎶 Now Playing from YouTube Music", description=f"**[{song['title']}]({song['info'].get('webpage_url', '')})**", color=discord.Color.red())
        else:
            embed = discord.Embed(title="🎶 Now Playing", description=f"**[{song['title']}]({song['info'].get('webpage_url', '')})**", color=discord.Color.green())
        if song['thumbnail']:
            embed.set_thumbnail(url=song['thumbnail'])
        await ctx.send(embed=embed)
    except Exception as e:
        logger.error(f"[Sonix] Error starting playback: {e}")
        if retry_count < 1:
            logger.info(f"[Sonix] Retrying playback for: {song['title']}")
            await play_song(ctx, song, retry_count=retry_count+1)
            return
        embed = discord.Embed(title="❌ Error", description=f"Could not start playback after retry.\n```{str(e)}```", color=discord.Color.red())
        await ctx.send(embed=embed)

def play_next(ctx):
    # Use a per-guild playback flag to prevent double starts
    if not hasattr(ctx.bot, 'is_playing_flag'):
        ctx.bot.is_playing_flag = {}
    flag = ctx.bot.is_playing_flag.setdefault(ctx.guild.id, False)
    if flag:
        # Already playing, don't trigger again
        return
    queue = get_queue(ctx)
    # Cancel elevator music if running
    if hasattr(ctx.bot, 'elevator_task') and ctx.bot.elevator_task:
        ctx.bot.elevator_task.cancel()
        ctx.bot.elevator_task = None
    if hasattr(ctx.bot, 'disconnect_task') and ctx.bot.disconnect_task:
        ctx.bot.disconnect_task.cancel()
        ctx.bot.disconnect_task = None
    if queue:
        ctx.bot.is_playing_flag[ctx.guild.id] = True
        ctx.bot.loop.create_task(play_next_with_delay(ctx))
        # Cancel disconnect timer if music starts
        if hasattr(ctx.bot, 'disconnect_task') and ctx.bot.disconnect_task:
            ctx.bot.disconnect_task.cancel()
            ctx.bot.disconnect_task = None
    else:
        now_playing[ctx.guild.id] = None
        ctx.bot.is_playing_flag[ctx.guild.id] = False
        # Only start elevator music if enabled, not prevented, and nothing is playing
        if is_elevator_enabled(ctx) and not getattr(ctx.bot, 'prevent_fallback', False):
            if ctx.voice_client and not ctx.voice_client.is_playing():
                ctx.bot.elevator_task = ctx.bot.loop.create_task(play_elevator_music(ctx))
        else:
            ctx.bot.disconnect_task = ctx.bot.loop.create_task(disconnect_after_timeout(ctx, 300))

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    logging.basicConfig(level=logging.INFO)

# This is where music commands will go

@bot.command(aliases=["m!j", "j"])
async def join(ctx):
    """Joins the voice channel you are in."""
    import logging
    logger = logging.getLogger("sonix_debug")
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        try:
            await channel.connect()
            embed = discord.Embed(title="🔊 Joined Voice Channel", description=f"Joined **{channel}**!", color=discord.Color.blurple())
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"[DEBUG] Failed to join voice channel: {e}")
            embed = discord.Embed(title="❌ Error", description=f"Failed to join voice channel: `{e}`", color=discord.Color.red())
            await ctx.send(embed=embed)
    else:
        embed = discord.Embed(title="❌ Error", description="You are not in a voice channel.", color=discord.Color.red())
        await ctx.send(embed=embed)

@bot.command(aliases=["m!p", "p"])
async def play(ctx, *, query):
    """
    Adds a song or Spotify track/album/playlist to the queue or plays if nothing is playing.
    Usage: !play <song name or URL>
    """
    queue = get_queue(ctx)
    await ctx.send("[DEBUG] Play command called.")
    logger.info("[DEBUG] Play command called.")
    # Check if user is in a voice channel and join if not already connected
    if ctx.voice_client is None:
        if ctx.author.voice and ctx.author.voice.channel:
            await ctx.send("[DEBUG] Joining your voice channel...")
            try:
                await ctx.author.voice.channel.connect()
                await ctx.send(f"[DEBUG] Joined voice channel: {ctx.author.voice.channel}")
            except Exception as e:
                await ctx.send(f"❌ Failed to join voice channel: `{e}`")
                import logging
                logging.getLogger("sonix_debug").error(f"[DEBUG] Failed to join voice channel: {e}")
                return
        else:
            await ctx.send("You are not in a voice channel.")
            return
    # Check if query is a Spotify link
    spotify_pattern = r"https://open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
    match = re.match(spotify_pattern, query)
    # Determine if the bot is already playing or paused
    is_playing = ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused())

    await ctx.send(f"[DEBUG] Fetching metadata for query: {query}")
    logger.info(f"[DEBUG] Fetching metadata for query: {query}")
    try:
        song = await fetch_song_metadata(query)
    except Exception as e:
        await ctx.send(f"[DEBUG] Exception during metadata fetch: {e}")
        logger.error(f"[DEBUG] Exception during metadata fetch: {e}")
        song = None
    if not song:
        await ctx.send(f"[DEBUG] Metadata fetch failed for query: {query}")
        logger.error(f"[DEBUG] Metadata fetch failed for query: {query}")
        embed = discord.Embed(title="❌ Error", description="Could not find song metadata.", color=discord.Color.red())
        await ctx.send(embed=embed)
        return


    async def fetch_multiple_song_metadata(queries):
        # Helper to fetch metadata for a list of queries in parallel
        results = await asyncio.gather(*(fetch_song_metadata(q) for q in queries))
        # Filter out None (failed) results
        return [song for song in results if song is not None]

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
                song = await fetch_song_metadata(search_query)
                queue.append(song)
                if is_playing:
                    embed = discord.Embed(title="🟢 Added from Spotify", description=f"**{song['title']}**", color=discord.Color.green())
                    if song['thumbnail']:
                        embed.set_thumbnail(url=song['thumbnail'])
                    await ctx.send(embed=embed)
            elif link_type == 'album':
                album = sp.album(spotify_id)
                tracks = album['tracks']['items']
                queries = [f"{t['name']} {t['artists'][0]['name']}" for t in tracks]
                songs = await fetch_multiple_song_metadata(queries)
                if songs:
                    queue.extend(songs)
                    if is_playing:
                        embed = discord.Embed(title="🟢 Added Album from Spotify", description=f"Added **{len(songs)}** tracks from album!", color=discord.Color.green())
                        await ctx.send(embed=embed)
                else:
                    embed = discord.Embed(title="❌ Spotify Album Error", description="No playable tracks found in this Spotify album. All may be unavailable or blocked.", color=discord.Color.red())
                    await ctx.send(embed=embed)
            elif link_type == 'playlist':
                playlist = sp.playlist(spotify_id)
                tracks = playlist['tracks']['items']
                queries = []
                for item in tracks:
                    t = item['track']
                    if t is not None:
                        queries.append(f"{t['name']} {t['artists'][0]['name']}")
                songs = await fetch_multiple_song_metadata(queries)
                if songs:
                    queue.extend(songs)
                    if is_playing:
                        embed = discord.Embed(title="🟢 Added Playlist from Spotify", description=f"Added **{len(songs)}** tracks from playlist!", color=discord.Color.green())
                        await ctx.send(embed=embed)
                else:
                    embed = discord.Embed(title="❌ Spotify Playlist Error", description="No playable tracks found in this Spotify playlist. All may be unavailable or blocked.", color=discord.Color.red())
                    await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(title="❌ Spotify Error", description=f"Error processing Spotify link.\n```{str(e)}```", color=discord.Color.red())
            await ctx.send(embed=embed)
            return
    else:
        # Always fetch metadata and store as song object
        song = await fetch_song_metadata(query)
        queue.append(song)
        if is_playing:
            embed = discord.Embed(title="➕ Added to Queue", description=f"**[{song['title']}]({song['webpage_url']})**", color=discord.Color.blurple())
            if song['thumbnail']:
                embed.set_thumbnail(url=song['thumbnail'])
            await ctx.send(embed=embed)
    # Check if the bot is already playing audio
    if ctx.voice_client and ctx.voice_client.is_playing():
        # Only add to queue if not already in queue
        song = await fetch_song_metadata(query)
        queue = get_queue(ctx)
        if song and song not in queue:
            add_to_queue(ctx, song)
            embed = discord.Embed(title="➕ Added to Queue", description=f"**[{song['title']}]({song['webpage_url']})**", color=discord.Color.blurple())
            if song['thumbnail']:
                embed.set_thumbnail(url=song['thumbnail'])
            await ctx.send(embed=embed)
        elif song:
            embed = discord.Embed(title="⚠️ Already Queued", description=f"**[{song['title']}]({song['webpage_url']})** is already in the queue.", color=discord.Color.orange())
            await ctx.send(embed=embed)
        else:
            embed = discord.Embed(title="❌ Error", description="Could not find song metadata.", color=discord.Color.red())
            await ctx.send(embed=embed)
        return
    # If nothing is playing, add to queue and start playback
    song = await fetch_song_metadata(query)
    if not song:
        embed = discord.Embed(title="❌ Error", description="Could not find song metadata.", color=discord.Color.red())
        await ctx.send(embed=embed)
        return
    add_to_queue(ctx, song)
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You are not in a voice channel.")
            return
    play_next(ctx)
    embed = discord.Embed(title="🎶 Now Playing", description=f"**[{song['title']}]({song['webpage_url']})**", color=discord.Color.green())
    if song['thumbnail']:
        embed.set_thumbnail(url=song['thumbnail'])
    await ctx.send(embed=embed)



# (Removed duplicate replay command definition)

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
        # Immediately clear playback flag and now_playing state to prevent race conditions
        if hasattr(ctx.bot, 'is_playing_flag'):
            ctx.bot.is_playing_flag[ctx.guild.id] = False
        now_playing[ctx.guild.id] = None
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

# --- Web API Integration: Start FastAPI in a background thread ---
def start_api(bot):
    import threading
    import uvicorn
    import api
    api.set_bot(bot)
    def run():
        uvicorn.run("api:app", host="0.0.0.0", port=8000, log_level="info")
    thread = threading.Thread(target=run, daemon=True)
    thread.start()

if __name__ == '__main__':
    start_api(bot)
    bot.run(TOKEN)