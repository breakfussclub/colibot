import discord
from discord import app_commands
from discord.ext import tasks
import aiohttp
from bs4 import BeautifulSoup
import os
from datetime import datetime, timedelta
import asyncio
from typing import List, Dict, Optional
import logging
import re
import asyncpg

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ColiBot')

# Environment variables
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
GUILD_ID = int(os.getenv('GUILD_ID'))
DATABASE_URL = os.getenv('DATABASE_URL')
POPULAR_POST_TIME = os.getenv('POPULAR_POST_TIME', '09:00')
NEWEST_POST_TIME = os.getenv('NEWEST_POST_TIME', '18:00')
POST_INTERVAL_HOURS = int(os.getenv('POST_INTERVAL_HOURS', '3'))  # Default every 3 hours
FORUM_URLS = os.getenv('FORUM_URLS', 'https://www.thecoli.com/forums/the-locker-room.6/').split(',')
TIME_FILTER_HOURS = int(os.getenv('TIME_FILTER_HOURS', '6'))  # Default 6 hours

# Global DB Pool
db_pool: Optional[asyncpg.Pool] = None

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)


async def init_db():
    """Initialize database connection and schema"""
    global db_pool
    if not DATABASE_URL:
        logger.error("DATABASE_URL not set!")
        return

    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        logger.info("Connected to database")
        
        async with db_pool.acquire() as conn:
            # Create threads table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS colibot_threads (
                    id SERIAL PRIMARY KEY,
                    thread_id VARCHAR(50) UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    author TEXT,
                    created_at TIMESTAMP WITH TIME ZONE,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create snapshots table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS colibot_thread_snapshots (
                    id SERIAL PRIMARY KEY,
                    thread_id VARCHAR(50) REFERENCES colibot_threads(thread_id),
                    replies INTEGER,
                    views INTEGER,
                    captured_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create index for faster querying
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_colibot_snapshots_thread_time 
                ON colibot_thread_snapshots(thread_id, captured_at)
            ''')
            
        logger.info("Database schema initialized")
            
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")



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
    
    def parse_number(self, text: str) -> int:
        """Parse number strings with K/M suffixes"""
        if not text:
            return 0
        
        text = text.upper().replace(',', '').strip()
        multiplier = 1
        
        if 'K' in text:
            multiplier = 1000
            text = text.replace('K', '')
        elif 'M' in text:
            multiplier = 1000000
            text = text.replace('M', '')
            
        try:
            return int(float(text) * multiplier)
        except ValueError:
            return 0

    async def fetch_page(self, url: str) -> str:
        await self.init_session()
        for attempt in range(3):
            try:
                async with self.session.get(url, timeout=30) as response:
                    if response.status == 200:
                        return await response.text()
                    else:
                        logger.error(f"Failed to fetch {url}: Status {response.status}")
                        if response.status >= 500:
                            # Retry on server errors
                            await asyncio.sleep(2 ** attempt)
                            continue
                        return None
            except Exception as e:
                logger.error(f"Error fetching {url} (Attempt {attempt+1}/3): {e}")
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return None
        return None
    
    def parse_thread_element(self, element, base_url: str) -> Dict:
        """Parse a single thread element and extract all data"""
        try:
            # Extract thread title and URL
            title_elem = element.find('a', {'data-tp-primary': 'on'})
            if not title_elem:
                title_elem = element.find('a', class_='structItem-title')
            
            if not title_elem:
                return None
            
            title = title_elem.get_text(strip=True)
            thread_url = title_elem.get('href', '')
            if thread_url and not thread_url.startswith('http'):
                thread_url = base_url + thread_url
            
            # Extract thread ID
            thread_id = None
            # URL format: .../thread-title.12345/
            match = re.search(r'\.(\d+)/?$', thread_url.split('/unread')[0])
            if match:
                thread_id = match.group(1)
            
            # Extract author
            author = "Unknown"
            author_elem = element.find('a', class_='username')
            if author_elem:
                author = author_elem.get_text(strip=True)
            
            # Extract replies and views from the meta section
            replies = 0
            views = 0
            
            # Look for the stats in structItem-cell--meta
            meta_cell = element.find('div', class_='structItem-cell--meta')
            if meta_cell:
                # Find all dd elements which contain the numbers
                dds = meta_cell.find_all('dd')
                if len(dds) >= 2:
                    try:
                        # First dd is usually replies, second is views
                        replies_text = dds[0].get_text(strip=True)
                        views_text = dds[1].get_text(strip=True)
                        
                        replies = self.parse_number(replies_text)
                        views = self.parse_number(views_text)
                    except (ValueError, AttributeError) as e:
                        logger.debug(f"Could not parse stats: {e}")
            
            # Extract timestamp for creation date
            time_elem = element.find('time', class_='structItem-startDate')
            if not time_elem:
                time_elem = element.find('time')
            
            created_timestamp = None
            timestamp_display = "Recently"
            
            if time_elem:
                # Try to get the datetime attribute
                datetime_str = time_elem.get('datetime')
                if datetime_str:
                    try:
                        created_timestamp = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
                    except:
                        pass
                
                # Get display text
                title_attr = time_elem.get('title')
                if title_attr:
                    timestamp_display = title_attr
                else:
                    timestamp_display = time_elem.get_text(strip=True)

            # Extract timestamp for last post date
            last_post_elem = element.find('time', class_='structItem-latestDate')
            last_post_timestamp = None
            
            if last_post_elem:
                datetime_str = last_post_elem.get('datetime')
                if datetime_str:
                    try:
                        last_post_timestamp = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
                    except:
                        pass

            return {
                'id': thread_id,
                'title': title,
                'url': thread_url,
                'author': author,
                'replies': replies,
                'views': views,
                'created_at': created_timestamp,
                'last_post_at': last_post_timestamp,
                'timestamp_display': timestamp_display,
                'score': replies + (views / 10)  # Popularity score
            }
        
        except Exception as e:
            logger.error(f"Error parsing thread element: {e}")
            return None
    
    async def get_trending_threads(self, limit: int = 5, hours: int = 6) -> List[Dict]:
        """Fetch trending threads (active in last N hours), sorted by popularity"""
        # Use order=last_post_date to get threads with recent activity
        url = f"{self.forum_url}?order=last_post_date&direction=desc"
        html = await self.fetch_page(url)
        
        if not html:
            return []
        
        soup = BeautifulSoup(html, 'html.parser')
        base_url = self.forum_url.split('/forums/')[0]
        
        threads = []
        cutoff_time = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(hours=hours)
        
        # Get all thread elements - get more to account for filtering
        # Fetch up to 50 threads to find the best ones
        thread_elements = soup.find_all('div', class_='structItem--thread', limit=50)
        
        logger.info(f"Found {len(thread_elements)} thread elements for trending")
        
        for element in thread_elements:
            thread_data = self.parse_thread_element(element, base_url)
            
            if thread_data:
                # Filter by last post time if we have a timestamp
                if thread_data.get('last_post_at'):
                    # Make cutoff_time timezone-aware if thread timestamp is
                    if thread_data['last_post_at'].tzinfo is not None and cutoff_time.tzinfo is None:
                        cutoff_time = cutoff_time.replace(tzinfo=thread_data['last_post_at'].tzinfo)
                    
                    if thread_data['last_post_at'] >= cutoff_time:
                        threads.append(thread_data)
                        logger.debug(f"Added trending thread candidate: {thread_data['title'][:50]}")
                    else:
                        # Since we are ordered by last post date, we can stop here
                        logger.debug(f"Thread too old (last post): {thread_data['title'][:50]}")
                        # break  # Uncommenting break for efficiency since we are sorted by last post
                else:
                    # If no timestamp, include it (might be recent)
                    threads.append(thread_data)
                    logger.debug(f"Added thread without timestamp: {thread_data['title'][:50]}")
        
        logger.info(f"After filtering: {len(threads)} trending threads in time window")
        
        # Sort by popularity score
        threads.sort(key=lambda x: x['score'], reverse=True)
        return threads[:limit]
    
    async def get_newest_threads(self, limit: int = 5, hours: int = 6) -> List[Dict]:
        """Fetch newest threads created in the last N hours"""
        # XenForo URL parameters: order by creation date
        url = f"{self.forum_url}?order=post_date&direction=desc"
        html = await self.fetch_page(url)
        
        if not html:
            return []
        
        soup = BeautifulSoup(html, 'html.parser')
        base_url = self.forum_url.split('/forums/')[0]
        
        threads = []
        cutoff_time = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(hours=hours)
        
        # Get thread elements
        thread_elements = soup.find_all('div', class_='structItem--thread', limit=limit * 3)
        
        logger.info(f"Found {len(thread_elements)} thread elements for newest")
        
        for element in thread_elements:
            thread_data = self.parse_thread_element(element, base_url)
            
            if thread_data:
                # Filter by creation time if available
                if thread_data['created_at']:
                    # Make cutoff_time timezone-aware if thread timestamp is
                    if thread_data['created_at'].tzinfo is not None and cutoff_time.tzinfo is None:
                        cutoff_time = cutoff_time.replace(tzinfo=thread_data['created_at'].tzinfo)
                    
                    if thread_data['created_at'] >= cutoff_time:
                        threads.append(thread_data)
                        logger.debug(f"Added new thread: {thread_data['title'][:50]}")
                    else:
                        logger.debug(f"Thread too old: {thread_data['title'][:50]}")
                else:
                    # If no timestamp parseable, include anyway
                    threads.append(thread_data)
                    logger.debug(f"Added thread without timestamp: {thread_data['title'][:50]}")
                
                if len(threads) >= limit:
                    break
        
        logger.info(f"After filtering: {len(threads)} newest threads")
        
        return threads[:limit]



async def save_thread_snapshot(thread_data: Dict):
    """Save thread data and snapshot to database"""
    if not db_pool or not thread_data.get('id'):
        return

    try:
        async with db_pool.acquire() as conn:
            # Check if the latest snapshot is identical to current data
            latest = await conn.fetchrow('''
                SELECT replies, views FROM colibot_thread_snapshots 
                WHERE thread_id = $1 
                ORDER BY captured_at DESC 
                LIMIT 1
            ''', thread_data['id'])
            
            if latest and latest['replies'] == thread_data['replies'] and latest['views'] == thread_data['views']:
                logger.debug(f"Skipping snapshot for thread {thread_data['id']} (no change)")
                return

            # Upsert thread info
            await conn.execute('''
                INSERT INTO colibot_threads (thread_id, title, url, author, created_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (thread_id) DO UPDATE 
                SET title = $2, url = $3, updated_at = CURRENT_TIMESTAMP
            ''', thread_data['id'], thread_data['title'], thread_data['url'], 
                 thread_data['author'], thread_data['created_at'])
            
            # Insert snapshot
            await conn.execute('''
                INSERT INTO colibot_thread_snapshots (thread_id, replies, views)
                VALUES ($1, $2, $3)
            ''', thread_data['id'], thread_data['replies'], thread_data['views'])
            
    except Exception as e:
        logger.error(f"Error saving snapshot for thread {thread_data.get('id')}: {e}")


async def get_trending_from_db(limit: int = 5, hours: int = 6) -> List[Dict]:
    """Get trending threads based on velocity (growth) from DB"""
    if not db_pool:
        return []

    try:
        async with db_pool.acquire() as conn:
            # Calculate growth: (current_replies - past_replies)
            # We look for the latest snapshot and compare it with one from ~hours ago
            rows = await conn.fetch('''
                WITH latest_snapshots AS (
                    SELECT DISTINCT ON (thread_id) thread_id, replies, views, captured_at
                    FROM colibot_thread_snapshots
                    ORDER BY thread_id, captured_at DESC
                ),
                past_snapshots AS (
                    SELECT DISTINCT ON (thread_id) thread_id, replies, views, captured_at
                    FROM colibot_thread_snapshots
                    WHERE captured_at <= NOW() - interval '1 hour' * $1
                    ORDER BY thread_id, captured_at DESC
                )
                SELECT 
                    t.title, t.url, t.author,
                    l.replies as current_replies,
                    l.views as current_views,
                    (l.replies - COALESCE(p.replies, 0)) as reply_growth,
                    (l.views - COALESCE(p.views, 0)) as view_growth
                FROM latest_snapshots l
                LEFT JOIN past_snapshots p ON l.thread_id = p.thread_id
                JOIN colibot_threads t ON l.thread_id = t.thread_id
                WHERE l.captured_at > NOW() - interval '1 hour'
                ORDER BY (reply_growth * 10 + view_growth) DESC
                LIMIT $2
            ''', float(hours), limit)
            
            results = []
            for row in rows:
                # Format growth display with K/M if needed
                reply_growth = row['reply_growth']
                view_growth = row['view_growth']
                
                reply_str = f"+{reply_growth}"
                view_str = f"+{view_growth}"
                
                if view_growth >= 1000:
                    view_str = f"+{view_growth/1000:.1f}K"
                
                results.append({
                    'title': row['title'],
                    'url': row['url'],
                    'author': row['author'],
                    'replies': row['current_replies'],
                    'views': row['current_views'],
                    'score': reply_growth, # Sort score is just raw reply growth
                    'growth_display': f"📈 {reply_str} replies, {view_str} views in last {hours}h"
                })
            
            return results
            
    except Exception as e:
        logger.error(f"Error fetching trending from DB: {e}")
        return []


@tasks.loop(hours=5)
async def scheduled_scraper():
    """Background task to scrape threads and save snapshots"""
    logger.info("Starting scheduled scrape for snapshots...")
    for forum_url in FORUM_URLS:
        try:
            scraper = ForumScraper(forum_url)
            # Fetch trending (active) threads to capture activity
            # We fetch a larger number to ensure we catch active threads from the last 24h
            threads = await scraper.get_trending_threads(limit=40, hours=24)
            await scraper.close_session()
            
            count = 0
            for thread in threads:
                if thread.get('id'):
                    await save_thread_snapshot(thread)
                    count += 1
            
            logger.info(f"Saved snapshots for {count} threads from {forum_url}")
            await asyncio.sleep(5) # Be nice to the server
            
        except Exception as e:
            logger.error(f"Error in scheduled_scraper for {forum_url}: {e}")


@tasks.loop(hours=24)
async def cleanup_old_data():
    """Delete snapshots older than 7 days to keep DB size manageable"""
    if not db_pool:
        return
        
    try:
        logger.info("Starting daily data cleanup...")
        async with db_pool.acquire() as conn:
            # Delete old snapshots
            result = await conn.execute('''
                DELETE FROM colibot_thread_snapshots 
                WHERE captured_at < NOW() - interval '7 days'
            ''')
            logger.info(f"Cleanup complete: {result}")
            
            # Optional: Delete threads that haven't been updated in 30 days
            # This keeps the main table clean of dead threads
            result_threads = await conn.execute('''
                DELETE FROM colibot_threads
                WHERE updated_at < NOW() - interval '30 days'
            ''')
            logger.info(f"Thread cleanup complete: {result_threads}")
            
    except Exception as e:
        logger.error(f"Error in cleanup_old_data: {e}")


def create_trending_embed(threads: List[Dict], forum_name: str, hours: int) -> discord.Embed:
    """Create Discord embed for trending threads"""
    embed = discord.Embed(
        title="🔥 Trending Threads",
        description=f"*Most popular discussions from the last {hours} hours*",
        color=0xFF6B35,  # Vibrant orange-red
        timestamp=datetime.utcnow()
    )
    
    # Add The Coli logo as thumbnail
    embed.set_thumbnail(url="https://raw.githubusercontent.com/breakfussclub/colibot/main/assets/colibot_logo.png")
    
    if not threads:
        embed.description = f"No threads found in the last {hours} hours."
        return embed
    
    for i, thread in enumerate(threads, 1):
        replies = thread.get('replies', 0)
        views = thread.get('views', 0)
        author = thread.get('author', 'Unknown')
        
        # Create more visually appealing field value
        if thread.get('growth_display'):
            stats = f"{thread['growth_display']}  •  💬 {replies:,} total  •  👁️ {views:,} views"
        else:
            stats = f"💬 **{replies:,}** replies  •  👁️ **{views:,}** views"
        
        embed.add_field(
            name=f"**{i}.** {thread['title'][:100]}",
            value=f"by {author}\n{stats}\n[→ View Thread]({thread['url']})",
            inline=False
        )
    
    embed.set_footer(text="The Coli • Updates daily", icon_url="https://raw.githubusercontent.com/breakfussclub/colibot/main/assets/colibot_logo.png")
    return embed


def create_newest_embed(threads: List[Dict], forum_name: str, hours: int) -> discord.Embed:
    """Create Discord embed for newest threads"""
    embed = discord.Embed(
        title="🆕 Newest Threads",
        description=f"*Fresh discussions from the last {hours} hours*",
        color=0x00D9FF,  # Bright cyan
        timestamp=datetime.utcnow()
    )
    
    # Add The Coli logo as thumbnail
    embed.set_thumbnail(url="https://raw.githubusercontent.com/breakfussclub/colibot/main/assets/colibot_logo.png")
    
    if not threads:
        embed.description = f"No new threads in the last {hours} hours."
        return embed
    
    for i, thread in enumerate(threads, 1):
        author = thread.get('author', 'Unknown')
        timestamp = thread.get('timestamp_display', 'Recently')
        
        embed.add_field(
            name=f"**{i}.** {thread['title'][:100]}",
            value=f"by {author}  •  🕐 {timestamp}\n[→ View Thread]({thread['url']})",
            inline=False
        )
    
    embed.set_footer(text="The Coli • Updates daily", icon_url="https://raw.githubusercontent.com/breakfussclub/colibot/main/assets/colibot_logo.png")
    return embed


@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Trending posts interval: Every {POST_INTERVAL_HOURS} hours')
    logger.info(f'Newest posts scheduled for: {NEWEST_POST_TIME}')
    logger.info(f'Time filter: Last {TIME_FILTER_HOURS} hours')
    logger.info(f'Monitoring forums: {FORUM_URLS}')
    
    # Sync slash commands to the guild
    guild = discord.Object(id=GUILD_ID)
    tree.copy_global_to(guild=guild)
    await tree.sync(guild=guild)
    logger.info(f'Slash commands synced to guild {GUILD_ID}')
    
    if not scheduled_posts.is_running():
        scheduled_posts.start()
    
    # Initialize DB and start scraper
    await init_db()
    if not scheduled_scraper.is_running():
        scheduled_scraper.start()
    if not cleanup_old_data.is_running():
        cleanup_old_data.start()


@tasks.loop(minutes=1)
async def scheduled_posts():
    """Check every minute if it's time to post"""
    now = datetime.now().strftime('%H:%M')
    channel = bot.get_channel(CHANNEL_ID)
    
    if not channel:
        logger.error(f"Channel {CHANNEL_ID} not found")
        return
    
    # Post trending threads - Check if we hit the interval hour
    # We check if minute is 0 and hour is divisible by interval
    now_dt = datetime.now()
    if now_dt.minute == 0 and now_dt.hour % POST_INTERVAL_HOURS == 0:
        logger.info("Posting trending threads...")
        for forum_url in FORUM_URLS:
            try:
                # Try to get from DB first
                threads = await get_trending_from_db(5, TIME_FILTER_HOURS)
                
                # Fallback if DB returns nothing (e.g. first run)
                if not threads:
                    logger.info("No DB stats yet, falling back to scraper")
                    scraper = ForumScraper(forum_url)
                    threads = await scraper.get_trending_threads(5, TIME_FILTER_HOURS)
                    await scraper.close_session()
                
                forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
                embed = create_trending_embed(threads, forum_name, TIME_FILTER_HOURS)
                await channel.send(embed=embed)
                
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Error posting trending threads for {forum_url}: {e}")
    
    # Post newest threads
    elif now == NEWEST_POST_TIME:
        logger.info("Posting newest threads...")
        for forum_url in FORUM_URLS:
            try:
                scraper = ForumScraper(forum_url)
                threads = await scraper.get_newest_threads(5, TIME_FILTER_HOURS)
                await scraper.close_session()
                
                forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
                embed = create_newest_embed(threads, forum_name, TIME_FILTER_HOURS)
                await channel.send(embed=embed)
                
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Error posting newest threads for {forum_url}: {e}")


@tree.command(
    name="force_trending",
    description="Manually post the top 5 trending threads from the last 6 hours",
    guild=discord.Object(id=GUILD_ID)
)
async def force_trending(interaction: discord.Interaction):
    """Force post trending threads"""
    await interaction.response.defer()
    
    for forum_url in FORUM_URLS:
        try:
            # Try to get from DB first
            threads = await get_trending_from_db(5, TIME_FILTER_HOURS)
            
            # Fallback
            if not threads:
                scraper = ForumScraper(forum_url)
                threads = await scraper.get_trending_threads(5, TIME_FILTER_HOURS)
                await scraper.close_session()
            
            forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
            embed = create_trending_embed(threads, forum_name, TIME_FILTER_HOURS)
            await interaction.followup.send(embed=embed)
            
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Error in force_trending for {forum_url}: {e}")
            await interaction.followup.send(f"❌ Error fetching threads: {str(e)}")


@tree.command(
    name="force_new",
    description="Manually post the 5 newest threads from the last 6 hours",
    guild=discord.Object(id=GUILD_ID)
)
async def force_new(interaction: discord.Interaction):
    """Force post newest threads"""
    await interaction.response.defer()
    
    for forum_url in FORUM_URLS:
        try:
            scraper = ForumScraper(forum_url)
            threads = await scraper.get_newest_threads(5, TIME_FILTER_HOURS)
            await scraper.close_session()
            
            forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
            embed = create_newest_embed(threads, forum_name, TIME_FILTER_HOURS)
            await interaction.followup.send(embed=embed)
            
            await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"Error in force_new for {forum_url}: {e}")
            await interaction.followup.send(f"❌ Error fetching threads: {str(e)}")


@tree.command(
    name="status",
    description="Check ColiBot's configuration and status",
    guild=discord.Object(id=GUILD_ID)
)
async def status(interaction: discord.Interaction):
    """Check bot status and configuration"""
    embed = discord.Embed(
        title="ColiBot Status",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Trending Posts Interval", value=f"Every {POST_INTERVAL_HOURS} hours", inline=False)
    embed.add_field(name="Newest Posts Time", value=NEWEST_POST_TIME, inline=False)
    embed.add_field(name="Time Filter", value=f"Last {TIME_FILTER_HOURS} hours", inline=False)
    embed.add_field(name="Monitored Forums", value='\n'.join(FORUM_URLS), inline=False)
    embed.add_field(name="Target Channel", value=f"<#{CHANNEL_ID}>", inline=False)
    embed.set_footer(text="ColiBot")
    await interaction.response.send_message(embed=embed)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN environment variable not set!")
        exit(1)
    if not CHANNEL_ID:
        logger.error("CHANNEL_ID environment variable not set!")
        exit(1)
    if not GUILD_ID:
        logger.error("GUILD_ID environment variable not set!")
        exit(1)
    
    bot.run(DISCORD_TOKEN)
