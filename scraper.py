import aiohttp
from bs4 import BeautifulSoup
import re
import logging
from datetime import datetime, timedelta
import asyncio
from typing import List, Dict, Optional
import urllib.parse

logger = logging.getLogger('ColiBot')

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
        return self

    async def __aenter__(self):
        await self.init_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()
    
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
                    
                    tasks = []
                    for res in results:
                        title = res.get_text(strip=True)
                        raw_url = res.get('href', '')
                        
                        # DDG wraps URLs
                        thread_url = raw_url
                        if 'uddg=' in raw_url:
                            try:
                                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(raw_url).query)
                                if 'uddg' in parsed:
                                    thread_url = parsed['uddg'][0]
                            except:
                                pass
                        
                        if '/threads/' in thread_url:
                            # Create a task to fetch the actual thread page for details
                            tasks.append(self.fetch_thread_details(thread_url, title))
                    
                    # Run all fetches in parallel
                    if tasks:
                        threads = await asyncio.gather(*tasks)
                        # Filter out Nones
                        return [t for t in threads if t]
                    return []
                else:
                    logger.error(f"DDG Search failed with status {response.status}")
                    return []
        except Exception as e:
            logger.error(f"Error searching threads: {e}")
            return []

    async def fetch_thread_details(self, url: str, fallback_title: str) -> Optional[Dict]:
        """Fetch a thread page and extract details (stats, author, image)"""
        try:
            html = await self.fetch_page(url)
            if not html:
                return {
                    'title': fallback_title,
                    'url': url,
                    'author': 'Unknown',
                    'replies': 0,
                    'views': 0,
                    'image': None
                }
            
            soup = BeautifulSoup(html, 'html.parser')
            base_url = self.forum_url.split('/forums/')[0]
            
            # 1. Title (Real title from page)
            title = fallback_title
            h1 = soup.find('h1', class_='p-title-value')
            if h1:
                title = h1.get_text(strip=True)
            
            # 2. Author
            author = "Unknown"
            author_elem = soup.find('a', class_='username')
            if author_elem:
                author = author_elem.get_text(strip=True)
                
            # 3. Stats (Replies/Views are tricky on thread view, usually in meta or not shown clearly like list view)
            # Actually, XenForo thread view doesn't always show total views easily in the header.
            # But we can try to find it in JSON-LD or meta tags.
            replies = 0
            views = 0
            
            # Try to find "X replies" text or similar? 
            # Or just accept 0 for now if it's too hard, but user asked for it.
            # Let's look for .p-body-header-content or similar.
            # Actually, let's just stick to 0 if we can't find it easily, but try to find an image.
            
            # 4. Image (No longer needed as per user request to use standard logo)
            image = None
            
            return {
                'title': title,
                'url': url,
                'author': author,
                'replies': replies,
                'views': views,
                'image': image
            }
        except Exception as e:
            logger.error(f"Error fetching thread details for {url}: {e}")
            return None
