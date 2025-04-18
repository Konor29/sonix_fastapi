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

bot = commands.Bot(command_prefix='!', intents=intents)

# Song queue per guild
song_queues = {}

# Helper to get the queue for a guild
def get_queue(ctx):
    return song_queues.setdefault(ctx.guild.id, [])

# Helper to play the next song in the queue
import asyncio

def play_next(ctx):
    queue = get_queue(ctx)
    if hasattr(ctx.bot, 'disconnect_task') and ctx.bot.disconnect_task:
        ctx.bot.disconnect_task.cancel()
        ctx.bot.disconnect_task = None
    if queue:
        next_query = queue.pop(0)
        ctx.bot.last_query = next_query
        ctx.bot.loop.create_task(play_song(ctx, next_query))
    else:
        # Wait 5 minutes before disconnecting
        async def delayed_disconnect():
            await asyncio.sleep(300)
            if ctx.voice_client and not get_queue(ctx):
                await ctx.voice_client.disconnect()
                channel = ctx.channel
                embed = discord.Embed(title="👋 Left Voice Channel", description="No songs were queued for 5 minutes. Disconnected to save resources.", color=discord.Color.orange())
                await channel.send(embed=embed)
        ctx.bot.disconnect_task = ctx.bot.loop.create_task(delayed_disconnect())

# Helper to play a song (used for both play and next)
async def play_song(ctx, query, retry_count=0):
    from yt_dlp import YoutubeDL
    import re
    logger = logging.getLogger("sonix_playback")
    # Detect if query is a YouTube Music URL
    is_ytmusic = isinstance(query, str) and re.match(r"https?://music\.youtube\.com/", query)
    # If not a URL, treat as a search query
    is_search = not (isinstance(query, str) and re.match(r"https?://", query))
    ydl_opts = {
        'format': 'bestaudio',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch' if is_search else None,
        'outtmpl': 'song.%(ext)s',
    }
    # For search queries, just use the plain string
    actual_query = query
    try:
        logger.info(f"[Sonix] Attempting to extract info for: {actual_query}")
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(actual_query, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            audio_url = info['url']
            title = info.get('title', 'Unknown Title')
        logger.info(f"[Sonix] Successfully extracted: {title}")
    except Exception as e:
        logger.error(f"[Sonix] Error extracting info: {e}")
        if retry_count < 1:
            logger.info(f"[Sonix] Retrying extraction for: {actual_query}")
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
        from yt_dlp import YoutubeDL
        import functools
        embed = discord.Embed(title="🎶 Song Queue", color=discord.Color.blurple())
        max_display = 10
        async def get_metadata(q):
            def fetch():
                with YoutubeDL({'quiet': True, 'noplaylist': True}) as ydl:
                    info = ydl.extract_info(q, download=False)
                    if 'entries' in info:
                        info = info['entries'][0]
                    return info
            try:
                info = await asyncio.to_thread(fetch)
                title = info.get('title', q)
                url = info.get('webpage_url', q)
                thumbnail = info.get('thumbnail')
                return title, url, thumbnail
            except Exception:
                return q, q, None
        fields = []
        for i, q in enumerate(queue[:max_display]):
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

bot.remove_command('help')

@bot.command()
async def help(ctx):
    """Shows all Sonix commands and features."""
    embed = discord.Embed(title="🤖 Sonix Help", description="Here are all my commands:", color=discord.Color.blurple())
    embed.add_field(name="!play <url or search>", value="Play a song from YouTube, Spotify, SoundCloud, etc. or search by name.", inline=False)
    embed.add_field(name="!pause / !resume", value="Pause or resume the current song.", inline=False)
    embed.add_field(name="!skip", value="Skip the current song.", inline=False)
    embed.add_field(name="!replay", value="Replay the last played song.", inline=False)
    embed.add_field(name="!stop", value="Stop the music and leave the voice channel.", inline=False)
    embed.add_field(name="!queue", value="Show the current song queue.", inline=False)
    embed.add_field(name="!join", value="Join your current voice channel.", inline=False)
    embed.set_footer(text="Sonix • Discord Music Bot")
    await ctx.send(embed=embed)

if __name__ == '__main__':
    bot.run(TOKEN)