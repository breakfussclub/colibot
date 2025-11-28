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
import urllib.parse
import json

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
POPULAR_POST_TIME = os.getenv('POPULAR_POST_TIME', '09:00')
NEWEST_POST_TIME = os.getenv('NEWEST_POST_TIME', '18:00')
POST_INTERVAL_HOURS = int(os.getenv('POST_INTERVAL_HOURS', '3'))  # Default every 3 hours
FORUM_URLS = os.getenv('FORUM_URLS', 'https://www.thecoli.com/forums/the-locker-room.6/').split(',')
TIME_FILTER_HOURS = int(os.getenv('TIME_FILTER_HOURS', '6'))  # Default 6 hours
# Global DB Connection
DATABASE_URL = os.getenv('DATABASE_URL')
pool = None


# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)



async def init_db():
    """Initialize PostgreSQL database pool and schema"""
    global pool
    try:
        if not DATABASE_URL:
            logger.error("DATABASE_URL not set!")
            return

        pool = await asyncpg.create_pool(DATABASE_URL)
        
        async with pool.acquire() as conn:
            # Create threads table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS colibot_threads (
                    id SERIAL PRIMARY KEY,
                    thread_id TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    author TEXT,
                    created_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create snapshots table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS colibot_thread_snapshots (
                    id SERIAL PRIMARY KEY,
                    thread_id TEXT REFERENCES colibot_threads(thread_id),
                    replies INTEGER,
                    views INTEGER,
                    captured_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create index for faster querying
            await conn.execute('''
                CREATE INDEX IF NOT EXISTS idx_colibot_snapshots_thread_time 
                ON colibot_thread_snapshots(thread_id, captured_at)
            ''')
            
        logger.info("PostgreSQL database initialized")
            
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
                'score': replies  # Popularity score (Replies only)
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

    async def search_threads(self, query: str, limit: int = 5) -> List[Dict]:
        """Search for threads by keyword using DuckDuckGo (bypasses login)"""
        try:
            # Use DuckDuckGo HTML version
            search_url = "https://html.duckduckgo.com/html/"
            params = {
                'q': f"site:thecoli.com {query}",
                'kl': 'us-en'
            }
            
            # Use a real browser User-Agent
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': 'https://html.duckduckgo.com/'
            }
            
            await self.init_session()
            async with self.session.post(search_url, data=params, headers=headers) as response:
                if response.status == 200:
                    html = await response.text()
                    soup = BeautifulSoup(html, 'html.parser')
                    
                    threads = []
                    # DDG results are in .result__a
                    results = soup.find_all('a', class_='result__a', limit=limit)
                    
                    for res in results:
                        title = res.get_text(strip=True)
                        raw_url = res.get('href', '')
                        
                        # DDG wraps URLs: //duckduckgo.com/l/?uddg=REAL_URL&rut=...
                        # Or sometimes direct links. Check if it's a wrapper.
                        thread_url = raw_url
                        if 'uddg=' in raw_url:
                            try:
                                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
                                if 'uddg' in parsed:
                                    thread_url = parsed['uddg'][0]
                            except:
                                pass
                        
                        # Only include actual thread links
                        if '/threads/' in thread_url:
                            threads.append({
                                'title': title,
                                'url': thread_url,
                                'author': 'Search Result', # DDG doesn't show author easily
                                'replies': 0, # Can't get stats from DDG
                                'views': 0
                            })
                            
                    return threads
                else:
                    logger.error(f"DDG Search failed with status {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error searching threads: {e}")
            return []





async def save_thread_snapshot(thread_data: Dict):
    """Save thread data and snapshot to database"""
    if not thread_data.get('id') or not pool:
        return

    try:
        async with pool.acquire() as conn:
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
                ON CONFLICT(thread_id) DO UPDATE 
                SET title = excluded.title, url = excluded.url, updated_at = CURRENT_TIMESTAMP
            ''', thread_data['id'], thread_data['title'], thread_data['url'], 
                 thread_data['author'], thread_data['created_at'])
            
            # Insert snapshot
            await conn.execute('''
                INSERT INTO colibot_thread_snapshots (thread_id, replies, views)
                VALUES ($1, $2, $3)
            ''', thread_data['id'], thread_data['replies'], thread_data['views'])
            
    except Exception as e:
        logger.error(f"Error saving snapshot for thread {thread_data.get('id')}: {e}")



def generate_chart_url(data: Dict[str, List[tuple]]) -> str:
    """Generate a QuickChart URL for thread growth"""
    try:
        # Prepare datasets
        datasets = []
        colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF']
        
        # Find the common time range
        all_times = set()
        for points in data.values():
            for t, _ in points:
                all_times.add(t)
        
        sorted_times = sorted(list(all_times))
        # Keep only last 10-15 points to avoid clutter
        if len(sorted_times) > 15:
            sorted_times = sorted_times[-15:]
            
        labels = [t.strftime('%H:%M') for t in sorted_times]
        
        for i, (title, points) in enumerate(data.items()):
            # Map points to the common time axis
            point_map = {t: r for t, r in points}
            data_points = []
            last_val = 0
            
            for t in sorted_times:
                if t in point_map:
                    last_val = point_map[t]
                    data_points.append(last_val)
                else:
                    # Fill gaps with last known value or None
                    data_points.append(last_val if last_val > 0 else None)
            
            datasets.append({
                'label': title[:20] + '...',
                'data': data_points,
                'borderColor': colors[i % len(colors)],
                'fill': False,
                'borderWidth': 2,
                'pointRadius': 0
            })
            
        config = {
            'type': 'line',
            'data': {
                'labels': labels,
                'datasets': datasets
            },
            'options': {
                'title': {
                    'display': True,
                    'text': 'Reply Growth (Last 6 Hours)',
                    'fontColor': '#fff'
                },
                'legend': {
                    'position': 'bottom',
                    'labels': {
                        'fontColor': '#fff',
                        'fontSize': 10
                    }
                },
                'scales': {
                    'xAxes': [{
                        'ticks': {'fontColor': '#ccc'}
                    }],
                    'yAxes': [{
                        'ticks': {'fontColor': '#ccc'}
                    }]
                }
            }
        }
        
        # QuickChart URL
        base = "https://quickchart.io/chart"
        params = {
            'c': json.dumps(config),
            'w': 500,
            'h': 300,
            'bkg': '#2f3136' # Discord dark mode background
        }
        return f"{base}?{urllib.parse.urlencode(params)}"
        
    except Exception as e:
        logger.error(f"Error generating chart: {e}")
        return None


async def get_trending_from_db(limit: int = 5, hours: int = 6) -> tuple[List[Dict], Optional[str]]:
    """Get trending threads based on velocity (growth) from DB"""
    if not pool:
        return [], None

    try:
        async with pool.acquire() as conn:
            # Postgres version using DISTINCT ON for cleaner logic
            
            query = '''
                WITH latest_snapshots AS (
                    SELECT DISTINCT ON (thread_id) thread_id, replies, views, captured_at
                    FROM colibot_thread_snapshots
                    ORDER BY thread_id, captured_at DESC
                ),
                past_snapshots AS (
                    SELECT DISTINCT ON (thread_id) thread_id, replies, views, captured_at
                    FROM colibot_thread_snapshots
                    WHERE captured_at <= NOW() - ($1 || ' hours')::INTERVAL
                    ORDER BY thread_id, captured_at DESC
                )
                SELECT 
                    t.title, t.url, t.author, t.thread_id,
                    l.replies as current_replies,
                    l.views as current_views,
                    CASE 
                        WHEN p.replies IS NOT NULL THEN (l.replies - p.replies)
                        WHEN t.created_at > NOW() - ($2 || ' hours')::INTERVAL THEN l.replies
                        ELSE 0 
                    END as reply_growth,
                    CASE 
                        WHEN p.views IS NOT NULL THEN (l.views - p.views)
                        WHEN t.created_at > NOW() - ($3 || ' hours')::INTERVAL THEN l.views
                        ELSE 0 
                    END as view_growth
                FROM latest_snapshots l
                LEFT JOIN past_snapshots p ON l.thread_id = p.thread_id
                JOIN colibot_threads t ON l.thread_id = t.thread_id
                WHERE l.captured_at > NOW() - INTERVAL '1 hour'
                ORDER BY reply_growth DESC, t.created_at DESC
                LIMIT $4
            '''
            
            rows = await conn.fetch(query, str(hours), str(hours), str(hours), limit)
            
            results = []
            thread_ids = []
            
            for row in rows:
                # Format growth display with K/M if needed
                reply_growth = row['reply_growth']
                view_growth = row['view_growth']
                
                # Heat System
                heat_emoji = ""
                if reply_growth >= 50:
                    heat_emoji = "🌋" # Eruption
                elif reply_growth >= 20:
                    heat_emoji = "🔥" # Hot
                elif reply_growth >= 5:
                    heat_emoji = "📈" # Rising
                elif reply_growth > 0:
                    heat_emoji = "🌱" # Growing
                
                growth_display = ""
                if reply_growth > 0:
                    growth_display = f"{heat_emoji} +{reply_growth} replies in last {hours}h"
                
                results.append({
                    'id': row['thread_id'], # Needed for chart history
                    'title': row['title'],
                    'url': row['url'],
                    'author': row['author'],
                    'replies': row['current_replies'],
                    'views': row['current_views'],
                    'score': reply_growth, 
                    'growth_display': growth_display,
                    'heat_emoji': heat_emoji
                })
                thread_ids.append(row['thread_id'])
            
            # Generate Chart
            chart_url = None
            if results:
                try:
                    # Fetch history for these threads
                    history_query = '''
                        SELECT thread_id, replies, captured_at 
                        FROM colibot_thread_snapshots 
                        WHERE thread_id = ANY($1::text[]) 
                        AND captured_at > NOW() - ($2 || ' hours')::INTERVAL
                        ORDER BY captured_at ASC
                    '''
                    
                    history_rows = await conn.fetch(history_query, thread_ids, str(hours))
                        
                    # Organize data for chart
                    chart_data = {} # {title: [(time, replies), ...]}
                    
                    # Map IDs to titles
                    id_to_title = {t['id']: t['title'] for t in results}
                    
                    for h_row in history_rows:
                        tid = h_row['thread_id']
                        replies = h_row['replies']
                        captured_at = h_row['captured_at'] # Already a datetime object in asyncpg
                        
                        if tid in id_to_title:
                            title = id_to_title[tid]
                            if title not in chart_data:
                                chart_data[title] = []
                            chart_data[title].append((captured_at, replies))
                            
                    if chart_data:
                        chart_url = generate_chart_url(chart_data)
                        
                except Exception as e:
                    logger.error(f"Error fetching history for chart: {e}")
            
            return results, chart_url
            
    except Exception as e:
        logger.error(f"Error fetching trending from DB: {e}")
        return [], None


@tasks.loop(minutes=30)
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
    if not pool:
        return

    try:
        logger.info("Starting daily data cleanup...")
        async with pool.acquire() as conn:
            # Delete old snapshots
            await conn.execute('''
                DELETE FROM colibot_thread_snapshots 
                WHERE captured_at < NOW() - INTERVAL '7 days'
            ''')
            
            # Optional: Delete threads that haven't been updated in 30 days
            await conn.execute('''
                DELETE FROM colibot_threads
                WHERE updated_at < NOW() - INTERVAL '30 days'
            ''')
            
            logger.info("Cleanup complete")
            
    except Exception as e:
        logger.error(f"Error in cleanup_old_data: {e}")
def create_trending_embed(threads: List[Dict], forum_name: str, hours: int, chart_url: str = None) -> discord.Embed:
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
            name=f"#{i}",
            value=f"[**{thread['title'][:100]}**]({thread['url']})\nby {author}\n{stats}",
            inline=False
        )
    
    embed.set_footer(text="The Coli • Updates daily", icon_url="https://raw.githubusercontent.com/breakfussclub/colibot/main/assets/colibot_logo.png")
    
    if chart_url:
        embed.set_image(url=chart_url)
        
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
            name=f"#{i}",
            value=f"[**{thread['title'][:100]}**]({thread['url']})\nby {author}  •  🕐 {timestamp}",
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
                threads, chart_url = await get_trending_from_db(5, TIME_FILTER_HOURS)
                
                # Fallback if DB returns nothing (e.g. first run)
                if not threads:
                    logger.info("No DB stats yet, falling back to scraper")
                    scraper = ForumScraper(forum_url)
                    threads = await scraper.get_trending_threads(5, TIME_FILTER_HOURS)
                    await scraper.close_session()
                
                forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
                embed = create_trending_embed(threads, forum_name, TIME_FILTER_HOURS, chart_url)
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
            threads, chart_url = await get_trending_from_db(5, TIME_FILTER_HOURS)
            
            # Fallback
            if not threads:
                scraper = ForumScraper(forum_url)
                threads = await scraper.get_trending_threads(5, TIME_FILTER_HOURS)
                await scraper.close_session()
            
            forum_name = forum_url.split('/')[-2].replace('-', ' ').title()
            embed = create_trending_embed(threads, forum_name, TIME_FILTER_HOURS, chart_url)
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


@tree.command(
    name="search",
    description="Search for threads on The Coli",
    guild=discord.Object(id=GUILD_ID)
)
async def search(interaction: discord.Interaction, query: str):
    """Search for threads"""
    await interaction.response.defer()
    
    try:
        # Use the first configured forum URL to get the base domain
        scraper = ForumScraper(FORUM_URLS[0])
        threads = await scraper.search_threads(query, limit=5)
        await scraper.close_session()
        
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
            replies = thread.get('replies', 0)
            views = thread.get('views', 0)
            
            embed.add_field(
                name=f"#{i}",
                value=f"[**{thread['title'][:100]}**]({thread['url']})\nby {author} • 💬 {replies:,} • 👁️ {views:,}",
                inline=False
            )
            
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
                scraper = ForumScraper(url) # URL doesn't matter much here, we just need the instance
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
                        
                await scraper.close_session()
                
        except Exception as e:
            logger.error(f"Error unfurling link: {e}")


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
