# ColiBot

ColiBot is a Discord bot that automatically posts popular and newest threads from The Coli forums at scheduled times. Stay updated on the hottest discussions without leaving Discord!

## Features

- 🔥 Posts top 5 most popular threads based on replies and views
- 🆕 Posts latest 5 newest threads
- ⏰ Configurable posting schedule
- 🔧 Supports multiple forums
- 📊 Beautiful Discord embeds with thread information
- 🧪 Test commands for administrators

## Setup

### Prerequisites

- Python 3.9+
- A Discord Bot Token
- A Discord Server with a channel for posts

### Discord Bot Setup

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to the "Bot" section and create a bot
4. Enable these Privileged Gateway Intents:
   - Message Content Intent
5. Copy the bot token
6. Go to OAuth2 → URL Generator
7. Select scopes: `bot`
8. Select permissions: `Send Messages`, `Embed Links`, `Read Message History`
9. Use the generated URL to invite the bot to your server

### Get Channel ID

1. Enable Developer Mode in Discord (User Settings → Advanced → Developer Mode)
2. Right-click on the channel where you want posts and click "Copy Channel ID"

### Local Testing

1. Clone the repository
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Create a `.env` file:
   ```env
   DISCORD_TOKEN=your_discord_bot_token_here
   CHANNEL_ID=your_channel_id_here
   POPULAR_POST_TIME=09:00
   NEWEST_POST_TIME=18:00
   FORUM_URLS=https://www.thecoli.com/forums/the-locker-room.6/
   ```

4. Run ColiBot:
   ```bash
   python colibot.py
   ```

### Railway Deployment

1. Push your code to GitHub
2. Create a new project on [Railway](https://railway.app)
3. Connect your GitHub repository
4. Add the following environment variables in Railway:
   - `DISCORD_TOKEN`: Your Discord bot token
   - `CHANNEL_ID`: Your Discord channel ID (just the number)
   - `POPULAR_POST_TIME`: Time for popular posts (format: HH:MM, 24-hour time)
   - `NEWEST_POST_TIME`: Time for newest posts (format: HH:MM, 24-hour time)
   - `FORUM_URLS`: Comma-separated list of forum URLs (optional, defaults to The Coli Locker Room)

5. Deploy!

## Environment Variables

| Variable | Description | Required | Default |
|----------|-------------|----------|---------|
| `DISCORD_TOKEN` | Your Discord bot token | ✅ Yes | - |
| `CHANNEL_ID` | Discord channel ID for posts | ✅ Yes | - |
| `POPULAR_POST_TIME` | Time to post popular threads (HH:MM) | ❌ No | `09:00` |
| `NEWEST_POST_TIME` | Time to post newest threads (HH:MM) | ❌ No | `18:00` |
| `FORUM_URLS` | Comma-separated forum URLs | ❌ No | The Coli Locker Room |

### Time Format

Times should be in 24-hour format (HH:MM):
- `09:00` = 9:00 AM
- `18:00` = 6:00 PM
- `00:00` = Midnight
- `12:00` = Noon

Times are in the timezone of your Railway deployment (typically UTC). Adjust accordingly.

### Multiple Forums

To monitor multiple forums, separate URLs with commas:
```
FORUM_URLS=https://www.thecoli.com/forums/the-locker-room.6/,https://www.thecoli.com/forums/the-booth.20/
```

## Commands

All commands require administrator permissions.

- `!test_popular` - Test fetching and posting popular threads
- `!test_newest` - Test fetching and posting newest threads
- `!status` - Check bot configuration and status

## File Structure

```
.
├── colibot.py         # Main bot code
├── requirements.txt   # Python dependencies
├── railway.json       # Railway configuration
├── README.md         # This file
└── .env              # Local environment variables (not committed)
```

## Troubleshooting

### Bot not posting at scheduled times

- Make sure times are in 24-hour format (e.g., `18:00` not `6:00 PM`)
- Check Railway logs for any errors
- Verify Railway deployment timezone (usually UTC)

### Channel not found error

- Ensure `CHANNEL_ID` is just the numeric ID (no extra characters)
- Verify the bot has access to the channel
- Check bot permissions in the server

### Scraping issues

- The bot is designed for XenForo forums
- If the forum structure changes, the scraper may need updates
- Check Railway logs for specific errors

## Adding New Forums

The bot is designed to work with XenForo-based forums. To add a new forum:

1. Add the forum URL to `FORUM_URLS` environment variable
2. Test with `!test_popular` and `!test_newest` commands
3. If the forum has a different structure, the scraper may need adjustments

## Notes

- The bot checks every minute if it's time to post
- Only posts once per scheduled time
- Rate limiting: 2-second delay between forum posts
- Threads are sorted by a popularity score (replies + views/10)

## License

MIT License - Feel free to modify and use as needed!
