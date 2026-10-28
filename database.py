import aiosqlite
from config import DATABASE_PATH, START_BALANCE
from datetime import datetime

async def init_db():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 0,
                total_wins INTEGER DEFAULT 0,
                total_losses INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0,
                last_daily TEXT,
                created_at TEXT,
                is_banned INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                reward_amount INTEGER,
                max_uses INTEGER,
                current_uses INTEGER DEFAULT 0,
                expires_at TEXT,
                created_by INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_activations (
                code TEXT,
                user_id INTEGER,
                activated_at TEXT,
                PRIMARY KEY (code, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gifts (
                gift_id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user_id INTEGER,
                to_user_id INTEGER,
                amount INTEGER,
                created_at TEXT
            )
        """)
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def get_user_by_username(username: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username,)) as cursor:
            return await cursor.fetchone()

async def create_user(user_id: int, username: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO users (user_id, username, balance, created_at) 
               VALUES (?, ?, ?, ?)""",
            (user_id, username.lower() if username else None, START_BALANCE, datetime.now().isoformat())
        )
        await db.commit()

async def update_username(user_id: int, username: str):
    if username:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username.lower(), user_id))
            await db.commit()

async def update_balance(user_id: int, amount: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.commit()

async def update_stats(user_id: int, won: bool):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        if won:
            await db.execute("UPDATE users SET total_wins = total_wins + 1, games_played = games_played + 1 WHERE user_id = ?", (user_id,))
        else:
            await db.execute("UPDATE users SET total_losses = total_losses + 1, games_played = games_played + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def update_daily(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET last_daily = ? WHERE user_id = ?", (datetime.now().isoformat(), user_id))
        await db.commit()

async def get_top_users(limit: int = 10):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT username, balance FROM users WHERE is_banned = 0 ORDER BY balance DESC LIMIT ?", (limit,)) as cursor:
            return await cursor.fetchall()

async def get_all_users():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT user_id FROM users WHERE is_banned = 0") as cursor:
            return await cursor.fetchall()

async def get_users_count():
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cursor:
            total = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1") as cursor:
            banned = (await cursor.fetchone())[0]
        active = total - banned
        async with db.execute("SELECT SUM(balance) FROM users WHERE is_banned = 0") as cursor:
            total_balance = (await cursor.fetchone())[0] or 0
        async with db.execute("SELECT SUM(games_played) FROM users") as cursor:
            total_games = (await cursor.fetchone())[0] or 0
        async with db.execute("SELECT SUM(total_wins) FROM users") as cursor:
            total_wins = (await cursor.fetchone())[0] or 0
        return {
            "total": total,
            "active": active,
            "banned": banned,
            "total_balance": total_balance,
            "total_games": total_games,
            "total_wins": total_wins
        }

async def create_promo(code: str, reward: int, max_uses: int, expires_at: str, created_by: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT INTO promo_codes (code, reward_amount, max_uses, expires_at, created_by) VALUES (?, ?, ?, ?, ?)", (code, reward, max_uses, expires_at, created_by))
        await db.commit()

async def get_promo(code: str):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT * FROM promo_codes WHERE code = ?", (code,)) as cursor:
            return await cursor.fetchone()

async def activate_promo(code: str, user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = ?", (code,))
        await db.execute("INSERT INTO promo_activations (code, user_id, activated_at) VALUES (?, ?, ?)", (code, user_id, datetime.now().isoformat()))
        await db.commit()

async def check_promo_activation(code: str, user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute("SELECT * FROM promo_activations WHERE code = ? AND user_id = ?", (code, user_id)) as cursor:
            return await cursor.fetchone()

async def create_gift(from_user_id: int, to_user_id: int, amount: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("INSERT INTO gifts (from_user_id, to_user_id, amount, created_at) VALUES (?, ?, ?, ?)", (from_user_id, to_user_id, amount, datetime.now().isoformat()))
        await db.commit()

async def ban_user(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET is_banned = 1 WHERE user_id = ?", (user_id,))
        await db.commit()

async def unban_user(user_id: int):
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("UPDATE users SET is_banned = 0 WHERE user_id = ?", (user_id,))
        await db.commit()