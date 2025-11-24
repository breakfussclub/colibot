import discord
from discord.ext import commands, tasks
import aiohttp
from bs4 import BeautifulSoup
import os
from datetime import datetime
import asyncio
from typing import List, Dict
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ColiBot')

# Environment variables
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
POPULAR_POST_TIME = os.getenv('POPULAR_POST_TIME', '09:00')  # Format: HH:MM
NEWEST_POST_TIME = os.getenv('NEWEST_POST_TIME', '18:00')    # Format: HH:MM
FORUM_URLS = os.getenv('FORUM_URLS', 'https://www.thecoli.com/forums/the-locker-room.6/').split(',')

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)


class ForumScraper:
    def __init__(self, forum_url: str):
        self.forum_url = forum_url.strip()
        self.session = None
    
    async def init_session(self):
        if not self.session:
            self.session = aiohttp.ClientSession(
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            )
    
    async def close_session(self):
        if self.session:
            await self.session.close()
    
    async def fetch_page(self, url: str) -> str:
        await self.init_session()
        try:
            async with self.session.get(url, timeout=30) as response:
                if response.status == 200:
                    return await response.text()
                else:
                    logger.error(f"Failed to fetch {url}: Status {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
    
    async def get_popular_threads(self, limit: int = 5) -> List[Dict]:
        """Fetch most popular threads (sorted by replies/views)"""
        html = await self.fetch_page(self.forum_url)
        if not html:
            return []
        
        soup = BeautifulSoup(html, 'html.parser')
        threads = []
        
        # XenForo thread structure
        thread_elements = soup.find_all('div', class_='structItem--thread', limit=limit * 2)
        
        for element in thread_elements:
            try:
                # Extract thread title and URL
                title_elem = element.find('a', {'data-tp-primary': 'on'})
                if not title_elem:
                    title_elem = element.find('a', class_='structItem-title')
                
                if not title_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                thread_url = title_elem.get('href', '')
                if thread_url and not thread_url.startswith('http'):
                    base_url = self.forum_url.split('/forums/')[0]
                    thread_url = base_url + thread_url
                
                # Extract replies and views
                stats = element.find('div', class_='structItem-cell--meta')
                replies = 0
                views = 0
                
                if stats:
                    replies_elem = stats.find('dd', text=lambda t: t and 'replies' in t.lower())
                    if not replies_elem:
                        replies_elem = stats.find('dl', class_='pairs--justified')
                        if replies_elem:
                            dds = replies_elem.find_all('dd')
                            if len(dds) >= 2:
                                try:
                                    replies = int(dds[0].get_text(strip=True).replace(',', ''))
                                    views = int(dds[1].get_text(strip=True).replace(',', ''))
                                except:
                                    pass
                
                # Extract author
                author = "Unknown"
                author_elem = element.find('a', class_='username')
                if author_elem:
                    author = author_elem.get_text(strip=True)
                
                threads.append({
                    'title': title,
                    'url': thread_url,
                    'replies': replies,
                    'views': views,
                    'author': author,
                    'score': replies + (views / 10)  # Popularity score
                })
            
            except Exception as e:
                logger.error(f"Error parsing thread: {e}")
                continue
        
        # Sort by popularity score
        threads.sort(key=lambda x: x['score'], reverse=True)
        return threads[:limit]
    
    async def get_newest_threads(self, limit: int = 5) -> List[Dict]:
        """Fetch newest threads"""
        html = await self.fetch_page(self.forum_url)
        if not html:
            return []
        
        soup = BeautifulSoup(html, 'html.parser')
        threads = []
        
        # XenForo thread structure - newest are typically at the top
        thread_elements = soup.find_all('div', class_='structItem--thread', limit=limit)
        
        for element in thread_elements:
            try:
                # Extract thread title and URL
                title_elem = element.find('a', {'data-tp-primary': 'on'})
                if not title_elem:
                    title_elem = element.find('a', class_='structItem-title')
                
                if not title_elem:
                    continue
                
                title = title_elem.get_text(strip=True)
                thread_url = title_elem.get('href', '')
                if thread_url and not thread_url.startswith('http'):
                    base_url = self.forum_url.split('/forums/')[0]
                    thread_url = base_url + thread_url
                
                # Extract author
                author = "Unknown"
                author_elem = element.find('a', class_='username')
                if author_elem:
                    author = author_elem.get_text(strip=True)
                
                # Extract timestamp
                time_elem = element.find('time')
                timestamp = "Just now"
                if time_elem:
                    timestamp = time_elem.get('title', time_elem.get_text(strip=True))
                
                threads.append({
                    'title': title,
                    'url': thread_url,
                    'author': author,
                    'timestamp': timestamp
                })
            
            except Exception as e:
                logger.error(f"Error parsing thread: {e}")
                continue
        
        return threads[:limit]


def create_popular_embed(threads: List[Dict], forum_name: str) -> discord.Embed:
    """Create Discord embed for popular threads"""
    embed = discord.Embed(
        title=f"🔥 Top 5 Popular Threads - {forum_name}",
        color=discord.Color.orange(),
        timestamp=datetime.utcnow()
    )
    
    if not threads:
        embed.description = "No threads found at this time."
        return embed
    
    for i, thread in enumerate(threads, 1):
        replies = thread.get('replies', 0)
        views = thread.get('views', 0)
        author = thread.get('author', 'Unknown')
        
        embed.add_field(
            name=f"{i}. {thread['title'][:100]}",
            value=f"👤 {author} | 💬 {replies:,} replies | 👁️ {views:,} views\n[View Thread]({thread['url']})",
            inline=False
        )
    
    embed.set_footer(text="Updates daily")
    return embed


def create_newest_embed(threads: List[Dict], forum_name: str) -> discord.Embed:
    """Create Discord embed for newest threads"""
    embed = discord.Embed(
        title=f"🆕 Latest 5 Threads - {forum_name}",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    
    if not threads:
        embed.description = "No threads found at this time."
        return embed
    
    for i, thread in enumerate(threads, 1):
        author = thread.get('author', 'Unknown')
        timestamp = thread.get('timestamp', 'Recently')
        
        embed.add_field(
            name=f"{i}. {thread['title'][:100]}",
            value=f"👤 {author} | 🕐 {timestamp}\n[View Thread]({thread['url']})",
            inline=False
        )
    
    embed.set_footer(text="Updates daily")
    return embed


@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Popular posts scheduled for: {POPULAR_POST_TIME}')
    logger.info(f'Newest posts scheduled for: {NEWEST_POST_TIME}')
    logger.info(f'Monitoring forums: {FORUM_URLS}')
    
    if not scheduled_posts.is_running():
        scheduled_posts.start()


@tasks.loop(minutes=1)
async def scheduled_posts():
    """Check every minute if it's time to post"""
    now = datetime.now().strftime('%H:%M')
    channel = bot.get_channel(CHANNEL_ID)
    
    if not channel:
        logger.error(f"Channel {CHANNEL_ID} not found")
        return
    
    # Post popular threads
    if now == POPULAR_POST_TIME:
        logger.info("Posting popular threads...")
        for forum_url in FORUM_URLS:
            try:
                scraper = ForumScraper(forum_url)
                threads = await scraper.get_popular_threads(5)
                await scraper.close_session()
                
                forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
                embed = create_popular_embed(threads, forum_name)
                await channel.send(embed=embed)
                
                await asyncio.sleep(2)  # Rate limit protection
            except Exception as e:
                logger.error(f"Error posting popular threads for {forum_url}: {e}")
    
    # Post newest threads
    elif now == NEWEST_POST_TIME:
        logger.info("Posting newest threads...")
        for forum_url in FORUM_URLS:
            try:
                scraper = ForumScraper(forum_url)
                threads = await scraper.get_newest_threads(5)
                await scraper.close_session()
                
                forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
                embed = create_newest_embed(threads, forum_name)
                await channel.send(embed=embed)
                
                await asyncio.sleep(2)  # Rate limit protection
            except Exception as e:
                logger.error(f"Error posting newest threads for {forum_url}: {e}")


@bot.command(name='test_popular')
@commands.has_permissions(administrator=True)
async def test_popular(ctx):
    """Test command to check popular threads"""
    await ctx.send("Fetching popular threads...")
    
    for forum_url in FORUM_URLS:
        scraper = ForumScraper(forum_url)
        threads = await scraper.get_popular_threads(5)
        await scraper.close_session()
        
        forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
        embed = create_popular_embed(threads, forum_name)
        await ctx.send(embed=embed)


@bot.command(name='test_newest')
@commands.has_permissions(administrator=True)
async def test_newest(ctx):
    """Test command to check newest threads"""
    await ctx.send("Fetching newest threads...")
    
    for forum_url in FORUM_URLS:
        scraper = ForumScraper(forum_url)
        threads = await scraper.get_newest_threads(5)
        await scraper.close_session()
        
        forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
        embed = create_newest_embed(threads, forum_name)
        await ctx.send(embed=embed)


@bot.command(name='status')
@commands.has_permissions(administrator=True)
async def status(ctx):
    """Check bot status and configuration"""
    embed = discord.Embed(
        title="Bot Status",
        color=discord.Color.blue()
    )
    embed.add_field(name="Popular Posts Time", value=POPULAR_POST_TIME, inline=False)
    embed.add_field(name="Newest Posts Time", value=NEWEST_POST_TIME, inline=False)
    embed.add_field(name="Monitored Forums", value='\n'.join(FORUM_URLS), inline=False)
    embed.add_field(name="Target Channel", value=f"<#{CHANNEL_ID}>", inline=False)
    await ctx.send(embed=embed)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN environment variable not set!")
        exit(1)
    if not CHANNEL_ID:
        logger.error("CHANNEL_ID environment variable not set!")
        exit(1)
    
    bot.run(DISCORD_TOKEN)
