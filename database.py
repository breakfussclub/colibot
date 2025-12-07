import os
import logging
import asyncpg
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from discord.ext import tasks

# Configure logging
logger = logging.getLogger('ColiBot')

from config import Config

# Global DB Connection
pool = None

async def ensure_pool():
    """Ensure database pool is healthy and initialized"""
    global pool
    if pool is None:
        await init_db()
    return pool

async def init_db():
    """Initialize PostgreSQL database pool and schema"""
    global pool
    try:
        if not Config.DATABASE_URL:
            logger.error("DATABASE_URL not set!")
            return

        pool = await asyncpg.create_pool(Config.DATABASE_URL)
        
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

async def save_thread_snapshot(thread_data: Dict):
    """Save thread data and snapshot to database"""
    if not thread_data.get('id'):
        return

    try:
        current_pool = await ensure_pool()
        if not current_pool:
            logger.error("Database pool not available")
            return
            
        async with current_pool.acquire() as conn:
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

async def get_trending_from_db(limit: int = 5, hours: int = 6) -> List[Dict]:
    """Get trending threads based on velocity (growth) from DB"""
    try:
        current_pool = await ensure_pool()
        if not current_pool:
            logger.error("Database pool not available")
            return []
            
        async with current_pool.acquire() as conn:
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
                    'id': row['thread_id'], 
                    'title': row['title'],
                    'url': row['url'],
                    'author': row['author'],
                    'replies': row['current_replies'],
                    'views': row['current_views'],
                    'score': reply_growth, 
                    'growth_display': growth_display,
                    'heat_emoji': heat_emoji
                })
            
            return results
            
    except Exception as e:
        logger.error(f"Error fetching trending from DB: {e}")
        return []

async def cleanup_old_data():
    """Delete snapshots older than 7 days to keep DB size manageable"""
    try:
        current_pool = await ensure_pool()
        if not current_pool:
            logger.error("Database pool not available for cleanup")
            return
            
        logger.info("Starting daily data cleanup...")
        async with current_pool.acquire() as conn:
            # Delete old snapshots
            deleted_snapshots = await conn.execute('''
                DELETE FROM colibot_thread_snapshots 
                WHERE captured_at < NOW() - INTERVAL '7 days'
            ''')
            
            # Optional: Delete threads that haven't been updated in 30 days
            deleted_threads = await conn.execute('''
                DELETE FROM colibot_threads
                WHERE updated_at < NOW() - INTERVAL '30 days'
            ''')
            
            logger.info(f"Cleanup complete - Removed old snapshots and threads")
            
    except Exception as e:
        logger.error(f"Error in cleanup_old_data: {e}")

@tasks.loop(hours=24)
async def cleanup_task():
    """Daily cleanup task loop"""
    await cleanup_old_data()

async def close_db():
    """Close the database connection pool"""
    global pool
    if pool:
        await pool.close()
        pool = None
        logger.info("Database connection pool closed")
