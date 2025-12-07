import discord
from discord import app_commands
from discord.ext import tasks
import os
from datetime import datetime
import asyncio
import logging
import re
from bs4 import BeautifulSoup

# Import new modules
from config import Config
from database import init_db, save_thread_snapshot, get_trending_from_db, cleanup_task, close_db
from scraper import ForumScraper
from utils import create_trending_embed, create_newest_embed

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ColiBot')

# Validate config
try:
    Config.validate()
except ValueError as e:
    logger.error(str(e))
    exit(1)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


@tasks.loop(minutes=30)
async def scheduled_scraper():
    """Background task to scrape threads and save snapshots"""
    logger.info("Starting scheduled scrape for snapshots...")
    for forum_url in Config.FORUM_URLS:
        try:
            async with ForumScraper(forum_url) as scraper:
                # Fetch trending (active) threads to capture activity
                # We fetch a larger number to ensure we catch active threads from the last 24h
                threads = await scraper.get_trending_threads(limit=40, hours=24)
            
            count = 0
            for thread in threads:
                if thread.get('id'):
                    await save_thread_snapshot(thread)
                    count += 1
            
            logger.info(f"Saved snapshots for {count} threads from {forum_url}")
            await asyncio.sleep(5) # Be nice to the server
            
        except Exception as e:
            logger.error(f"Error in scheduled_scraper for {forum_url}: {e}")


@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=Config.BOT_STATUS))
    logger.info(f'Set bot status to: Watching {Config.BOT_STATUS}')
    logger.info(f'Trending posts interval: Every {Config.POST_INTERVAL_HOURS} hours')
    logger.info(f'Newest posts scheduled for: {Config.NEWEST_POST_TIME}')
    logger.info(f'Time filter: Last {Config.TIME_FILTER_HOURS} hours')
    logger.info(f'Monitoring forums: {Config.FORUM_URLS}')
    
    # Verify database connection before starting
    try:
        await init_db()
        test = await get_trending_from_db(1, 1)
        logger.info("Database connection verified")
    except Exception as e:
        logger.critical(f"Database initialization failed: {e}")
        await bot.close()
        return
    
    # Sync slash commands to the guild
    guild = discord.Object(id=Config.GUILD_ID)
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)
    logger.info(f'Slash commands synced to guild {Config.GUILD_ID}')
    
    # Start background tasks
    if not scheduled_posts.is_running():
        scheduled_posts.start()
    
    if not scheduled_scraper.is_running():
        scheduled_scraper.start()
    
    if not cleanup_task.is_running():
        cleanup_task.start()
        logger.info("Cleanup task started (runs daily)")

@bot.event
async def on_disconnect():
    """Graceful shutdown handler"""
    logger.info("Bot disconnecting, closing database...")
    await close_db()

@tasks.loop(minutes=1)
async def scheduled_posts():
    """Check every minute if it's time to post"""
    now = datetime.now().strftime('%H:%M')
    channel = bot.get_channel(Config.CHANNEL_ID)
    
    if not channel:
        logger.error(f"Channel {Config.CHANNEL_ID} not found")
        return
    
    # Post trending threads - Check if we hit the interval hour
    # We check if minute is 0 and hour is divisible by interval
    now_dt = datetime.now()
    if now_dt.minute == 0 and now_dt.hour % Config.POST_INTERVAL_HOURS == 0:
        logger.info("Posting trending threads...")
        for forum_url in Config.FORUM_URLS:
            try:
                # Try to get from DB first
                threads = await get_trending_from_db(5, Config.TIME_FILTER_HOURS)
                
                # Fallback if DB returns nothing (e.g. first run)
                if not threads:
                    logger.info("No DB stats yet, falling back to scraper")
                    async with ForumScraper(forum_url) as scraper:
                        threads = await scraper.get_trending_threads(5, Config.TIME_FILTER_HOURS)
                
                forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
                embed = create_trending_embed(threads, forum_name, Config.TIME_FILTER_HOURS)
                await channel.send(embed=embed)
                
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Error posting trending threads for {forum_url}: {e}")
    
    # Post newest threads
    elif now == Config.NEWEST_POST_TIME:
        logger.info("Posting newest threads...")
        for forum_url in Config.FORUM_URLS:
            try:
                async with ForumScraper(forum_url) as scraper:
                    threads = await scraper.get_newest_threads(5, Config.TIME_FILTER_HOURS)
                
                forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
                embed = create_newest_embed(threads, forum_name, Config.TIME_FILTER_HOURS)
                await channel.send(embed=embed)
                
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Error posting newest threads for {forum_url}: {e}")


@tree.command(
    name="force_trending",
    description="Manually post the top 5 trending threads from the last 6 hours",
    guild=discord.Object(id=Config.GUILD_ID)
)
async def force_trending(interaction: discord.Interaction, channel: discord.TextChannel = None):
    """Force post trending threads"""
    await interaction.response.defer()
    
    for forum_url in Config.FORUM_URLS:
        try:
            # Try to get from DB first
            threads = await get_trending_from_db(5, Config.TIME_FILTER_HOURS)
            
            # Fallback
            if not threads:
                async with ForumScraper(forum_url) as scraper:
                    threads = await scraper.get_trending_threads(5, Config.TIME_FILTER_HOURS)
            
            forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
            embed = create_trending_embed(threads, forum_name, Config.TIME_FILTER_HOURS)
            
            if channel:
                await channel.send(embed=embed)
                await interaction.followup.send(f"✅ Posted trending threads to {channel.mention}")
            else:
                await interaction.followup.send(embed=embed)
            
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Error in force_trending for {forum_url}: {e}")
            await interaction.followup.send(f"❌ Error fetching threads: {str(e)}")


@tree.command(
    name="force_new",
    description="Manually post the 5 newest threads from the last 6 hours",
    guild=discord.Object(id=Config.GUILD_ID)
)
async def force_new(interaction: discord.Interaction):
    """Force post newest threads"""
    await interaction.response.defer()
    
    for forum_url in Config.FORUM_URLS:
        try:
            async with ForumScraper(forum_url) as scraper:
                threads = await scraper.get_newest_threads(5, Config.TIME_FILTER_HOURS)
            
            forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
            embed = create_newest_embed(threads, forum_name, Config.TIME_FILTER_HOURS)
            await interaction.followup.send(embed=embed)
            
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Error in force_new for {forum_url}: {e}")
            await interaction.followup.send(f"❌ Error fetching threads: {str(e)}")


@tree.command(
    name="status",
    description="Check ColiBot's configuration and status",
    guild=discord.Object(id=Config.GUILD_ID)
)
async def status(interaction: discord.Interaction):
    """Check bot status and configuration"""
    embed = discord.Embed(
        title="ColiBot Status",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Trending Posts Interval", value=f"Every {Config.POST_INTERVAL_HOURS} hours", inline=False)
    embed.add_field(name="Newest Posts Time", value=Config.NEWEST_POST_TIME, inline=False)
    embed.add_field(name="Time Filter", value=f"Last {Config.TIME_FILTER_HOURS} hours", inline=False)
    embed.add_field(name="Monitored Forums", value='\n'.join(Config.FORUM_URLS), inline=False)
    embed.add_field(name="Target Channel", value=f"<#{Config.CHANNEL_ID}>", inline=False)
    embed.set_footer(text="ColiBot")
    await interaction.response.send_message(embed=embed)


@tree.command(
    name="search",
    description="Search for threads on The Coli",
    guild=discord.Object(id=Config.GUILD_ID)
)
async def search(interaction: discord.Interaction, query: str):
    """Search for threads"""
    await interaction.response.defer()
    
    try:
        # Use the first configured forum URL to get the base domain
        async with ForumScraper(Config.FORUM_URLS[0]) as scraper:
            threads = await scraper.search_threads(query, limit=5)
        
        if not threads:
            await interaction.followup.send(f"No results found for '{query}'.")
            return
            
        embed = discord.Embed(
            title=f"🔍 Search Results: {query}",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        
        for i, thread in enumerate(threads, 1):
            author = thread.get('author', 'Unknown')
            
            embed.add_field(
                name=f"#{i}",
                value=f"[**{thread['title'][:100]}**]({thread['url']})\nby {author}",
                inline=False
            )
            
        # Use standard logo as thumbnail
        embed.set_thumbnail(url="https://raw.githubusercontent.com/breakfussclub/colibot/main/assets/colibot_logo.png")
        embed.set_footer(text="ColiBot • Search Results", icon_url="https://raw.githubusercontent.com/breakfussclub/colibot/main/assets/colibot_logo.png")
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.error(f"Error in search command: {e}")
        await interaction.followup.send(f"❌ Error searching: {str(e)}")


@bot.event
async def on_message(message):
    # Ignore messages from bot itself
    if message.author == bot.user:
        return

    # Smart Link Unfurling
    # Check if message contains a link to thecoli.com/threads/
    if "thecoli.com/threads/" in message.content:
        try:
            # Extract URL
            urls = re.findall(r'https?://(?:www\.)?thecoli\.com/threads/[^\s]+', message.content)
            
            for url in urls[:1]: # Only unfurl the first link to avoid spam
                # Scrape the thread details
                async with ForumScraper(url) as scraper: # URL doesn't matter much here, we just need the instance
                    html = await scraper.fetch_page(url)
                
                if html:
                    soup = BeautifulSoup(html, 'html.parser')
                    # We need to find the thread title and stats
                    # The page structure is different for a single thread view vs list view
                    # But we can grab the title from <h1 class="p-title-value">
                    
                    title_elem = soup.find('h1', class_='p-title-value')
                    if title_elem:
                        title = title_elem.get_text(strip=True)
                        
                        # Author is usually in the first post
                        author = "Unknown"
                        author_elem = soup.find('a', class_='username')
                        if author_elem:
                            author = author_elem.get_text(strip=True)
                            
                        # Create a simple embed
                        embed = discord.Embed(
                            title=title,
                            url=url,
                            color=0xFF6B35
                        )
                        embed.set_author(name=f"Thread by {author}")
                        embed.set_footer(text="The Coli Unfurler")
                        
                        await message.channel.send(embed=embed)
                        
        except Exception as e:
            logger.error(f"Error unfurling link: {e}")


if __name__ == "__main__":
    if not Config.DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN environment variable not set!")
        exit(1)
    
    bot.run(Config.DISCORD_TOKEN)
