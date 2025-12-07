import discord
from datetime import datetime
from typing import List, Dict
import logging

logger = logging.getLogger('ColiBot')

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
    
    # Ensure we don't exceed Discord embed limits
    total_chars = 0
    max_threads = threads
    
    for i, thread in enumerate(threads, 1):
        replies = thread.get('replies', 0)
        views = thread.get('views', 0)
        author = thread.get('author', 'Unknown')
        
        # Truncate title if needed
        title = thread['title'][:100] if len(thread['title']) > 100 else thread['title']
        
        # Create more visually appealing field value
        if thread.get('growth_display'):
            stats = f"{thread['growth_display']}  •  💬 {replies:,} total  •  👁️ {views:,} views"
        else:
            stats = f"💬 **{replies:,}** replies  •  👁️ **{views:,}** views"
        
        field_value = f"[**{title}**]({thread['url']})\nby {author}\n{stats}"
        
        # Check if adding this field would exceed limits
        total_chars += len(title) + len(field_value)
        if total_chars > 5000:  # Safety margin before Discord's 6000 char limit
            logger.warning(f"Embed approaching size limit, truncating at {i-1} threads")
            max_threads = threads[:i-1]
            break
        
        embed.add_field(
            name=f"#{i}",
            value=field_value,
            inline=False
        )
    
    embed.set_footer(text="ColiBot • Trending Threads", icon_url="https://raw.githubusercontent.com/breakfussclub/colibot/main/assets/colibot_logo.png")
    
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
    
    # Ensure we don't exceed Discord embed limits
    total_chars = 0
    
    for i, thread in enumerate(threads, 1):
        author = thread.get('author', 'Unknown')
        timestamp = thread.get('timestamp_display', 'Recently')
        
        # Truncate title if needed
        title = thread['title'][:100] if len(thread['title']) > 100 else thread['title']
        
        field_value = f"[**{title}**]({thread['url']})\nby {author}  •  🕐 {timestamp}"
        
        # Check if adding this field would exceed limits
        total_chars += len(title) + len(field_value)
        if total_chars > 5000:  # Safety margin before Discord's 6000 char limit
            logger.warning(f"Embed approaching size limit, truncating at {i-1} threads")
            break
        
        embed.add_field(
            name=f"#{i}",
            value=field_value,
            inline=False
        )
    
    embed.set_footer(text="ColiBot • Newest Threads", icon_url="https://raw.githubusercontent.com/breakfussclub/colibot/main/assets/colibot_logo.png")
    return embed
