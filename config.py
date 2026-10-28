import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",")]
DAILY_BONUS = int(os.getenv("DAILY_BONUS", "100"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "casino.db")
START_BALANCE = int(os.getenv("START_BALANCE", "1000"))