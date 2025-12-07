import os
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

class Config:
    # Discord
    DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
    CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
    GUILD_ID = int(os.getenv('GUILD_ID', '0'))
    
    # Bot Settings
    POPULAR_POST_TIME = os.getenv('POPULAR_POST_TIME', '09:00')
    NEWEST_POST_TIME = os.getenv('NEWEST_POST_TIME', '18:00')
    POST_INTERVAL_HOURS = int(os.getenv('POST_INTERVAL_HOURS', '3'))
    TIME_FILTER_HOURS = int(os.getenv('TIME_FILTER_HOURS', '6'))
    BOT_STATUS = os.getenv('BOT_STATUS', 'The Coli')
    
    # Target Forums
    FORUM_URLS = os.getenv('FORUM_URLS', 'https://www.thecoli.com/forums/the-locker-room.6/').split(',')
    
    # Database
    DATABASE_URL = os.getenv('DATABASE_URL')

    @classmethod
    def validate(cls):
        """Validate required configuration"""
        missing = []
        if not cls.DISCORD_TOKEN:
            missing.append("DISCORD_TOKEN")
        if not cls.CHANNEL_ID:
            missing.append("CHANNEL_ID")
        if not cls.GUILD_ID:
            missing.append("GUILD_ID")
        if not cls.DATABASE_URL:
            missing.append("DATABASE_URL")
            
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
