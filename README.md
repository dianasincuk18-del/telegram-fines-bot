# Telegram Fines Bot

Files:
- main.py
- requirements.txt
- render.yaml

Required Render environment variables:
- BOT_TOKEN
- SPREADSHEET_ID
- GOOGLE_CREDENTIALS_JSON
- WEBHOOK_SECRET

After deploy, set webhook:
https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://YOUR-RENDER-URL/telegram-webhook