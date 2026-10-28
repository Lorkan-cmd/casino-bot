import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from config import BOT_TOKEN, ADMIN_IDS, DAILY_BONUS
from database import (
    init_db, get_user, get_user_by_username, create_user, update_username,
    update_balance, update_stats, update_daily, get_top_users, get_all_users, get_users_count,
    create_promo, get_promo, activate_promo, check_promo_activation,
    create_gift
)
from keyboards import (
    main_menu_keyboard, solo_games_keyboard, group_games_keyboard, bet_keyboard,
    coin_keyboard, dice_keyboard, roulette_keyboard, slots_keyboard, blackjack_keyboard,
    back_to_menu_keyboard, admin_menu_keyboard,
    duel_response_keyboard, survival_keyboard, crash_keyboard,
    mines_count_keyboard, mines_field_keyboard
)
from datetime import datetime, timedelta
import random

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

player_bets = {}
broadcast_users = set()
pending_duels = {}
survival_games = {}
waiting_for_bet = {}
active_jackpots = {}
active_races = {}
active_battles = {}
active_wheels = {}
active_group_sessions = {}  # {chat_id: count} - счётчик одновременных игр в группе
MAX_GROUP_SESSIONS = 2  # Максимум одновременных игр в одной группе

SLOT_SYMBOLS = ["🍒", "🍋", "", "💎", "7️⃣"]
DUEL_COMMISSION = 0.05
SURVIVAL_COMMISSION = 0.10
JACKPOT_COMMISSION = 0.10
RACE_COMMISSION = 0.10
BATTLE_COMMISSION = 0.10
WHEEL_COMMISSION = 0.10
BET_TIMEOUT = 60

def create_deck():
    suits = ['♠', '♥', '♦', '♣']
    ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    deck = [f"{rank}{suit}" for suit in suits for rank in ranks]
    random.shuffle(deck)
    return deck

def get_card_value(card):
    rank = card[:-1]
    if rank in ['J', 'Q', 'K']: return 10
    elif rank == 'A': return 11
    return int(rank)

def calculate_score(hand):
    score = sum(get_card_value(card) for card in hand)
    aces = sum(1 for card in hand if card.endswith('A'))
    while score > 21 and aces:
        score -= 10
        aces -= 1
    return score

def format_hand(hand):
    return " ".join(hand)

def calculate_mines_multiplier(revealed_count, total_cells, mines_count):
    if revealed_count == 0:
        return 1.0
    safe_cells = total_cells - mines_count
    probability = 1.0
    for i in range(revealed_count):
        probability *= (safe_cells - i) / (total_cells - i)
    multiplier = (1.0 / probability) * 0.95
    return round(multiplier, 2)

def get_active_sessions(chat_id):
    """Подсчёт активных игр в чате"""
    count = 0
    if chat_id in active_jackpots: count += 1
    if chat_id in active_races: count += 1
    if chat_id in active_battles: count += 1
    if chat_id in active_wheels: count += 1
    if chat_id in survival_games and survival_games[chat_id].get("active"): count += 1
    return count

async def safe_send(message, text, **kwargs):
    try:
        return await message.answer(text, **kwargs)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await message.answer(text, **kwargs)
    except Exception:
        return None

async def safe_send_to_user(user_id, text, **kwargs):
    try:
        return await bot.send_message(user_id, text, **kwargs)
    except Exception:
        return None

async def crash_growth(user_id, message):
    try:
        while True:
            if user_id not in player_bets: break
            game = player_bets[user_id]
            if game.get("game") != "crash": break
            if game.get("crash_cashed_out") or game.get("crash_crashed"): break
            game["crash_multiplier"] = round(game["crash_multiplier"] + 0.05, 2)
            if game["crash_multiplier"] >= game["crash_point"]:
                game["crash_crashed"] = True
                bet = game["bet"]
                await update_stats(user_id, False)
                user = await get_user(user_id)
                await safe_send(message, f" <b>ВЗРЫВ!</b>\n\nРакета взорвалась на <b>x{game['crash_point']}</b>\n\n😔 Ты не успел забрать!\n💸 Проигрыш: -{bet} монет\n💰 Баланс: {user[2]} монет", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
                if user_id in player_bets: del player_bets[user_id]
                break
            await asyncio.sleep(0.3)
    except Exception as e:
        logger.error(f"Ошибка в crash_growth: {e}")

async def jackpot_timer(chat_id):
    jp = active_jackpots.get(chat_id)
    if not jp: return
    wait_time = (jp["end_time"] - datetime.now()).total_seconds()
    if wait_time > 0:
        await asyncio.sleep(wait_time)
    jp = active_jackpots.get(chat_id)
    if not jp: return
    participants = jp["participants"]
    bet = jp["bet"]
    if len(participants) < 2:
        for uid in participants:
            await update_balance(uid, bet)
        try:
            await bot.send_message(chat_id, " <b>Джекпот отменён!</b>\n\nНе набралось минимум 2 участников.\n💰 Ставки возвращены.", parse_mode="HTML")
        except Exception: pass
    else:
        winner_id = random.choice(list(participants.keys()))
        winner_name = participants[winner_id]
        total_bank = len(participants) * bet
        commission = int(total_bank * JACKPOT_COMMISSION)
        prize = total_bank - commission
        await update_balance(winner_id, prize)
        await update_stats(winner_id, True)
        try:
            await bot.send_message(chat_id, f"🎉 <b>ДЖЕКПОТ РАЗЫГРАН!</b> 🎉\n\n Победитель: <b>{winner_name}</b>!\n💰 Банк: {total_bank} монет\n📉 Комиссия бота: {commission} монет\n🎁 Выигрыш: {prize} монет", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
        except Exception: pass
    if chat_id in active_jackpots:
        del active_jackpots[chat_id]

async def race_animation(chat_id):
    try:
        race = active_races.get(chat_id)
        if not race: return
        participants = race["participants"]
        bet = race["bet"]
        user_ids = list(participants.keys())
        track_length = 10
        positions = {uid: 0 for uid in user_ids}
        start_msg = "🏇 <b>ГОНКА НАЧАЛАСЬ!</b>\n\n"
        for uid in user_ids:
            start_msg += f"🏇 {participants[uid]}: {'' * track_length} (0%)\n"
        start_msg += "\n⏳ Гонка идёт..."
        try:
            await bot.send_message(chat_id, start_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки стартового сообщения: {e}")
        for step in range(6):
            await asyncio.sleep(1.5)
            for uid in user_ids:
                if positions[uid] < track_length:
                    positions[uid] += random.randint(1, 3)
                    if positions[uid] > track_length:
                        positions[uid] = track_length
            progress_msg = f" <b>Гонка... шаг {step + 1}/6</b>\n\n"
            for uid in user_ids:
                pos = positions[uid]
                progress = int((pos / track_length) * 100)
                filled = '🟢' * pos
                empty = '' * (track_length - pos)
                progress_msg += f"🏇 {participants[uid]}: {filled}{empty} ({progress}%)\n"
            try:
                await bot.send_message(chat_id, progress_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Ошибка отправки прогресса: {e}")
            finished = [uid for uid in user_ids if positions[uid] >= track_length]
            if len(finished) >= 1:
                break
        max_pos = max(positions.values())
        winners = [uid for uid in user_ids if positions[uid] == max_pos]
        winner_id = random.choice(winners)
        winner_name = participants[winner_id]
        await asyncio.sleep(1)
        total_bank = len(participants) * bet
        commission = int(total_bank * RACE_COMMISSION)
        prize = total_bank - commission
        await update_balance(winner_id, prize)
        await update_stats(winner_id, True)
        for uid in user_ids:
            if uid != winner_id:
                await update_stats(uid, False)
        try:
            await bot.send_message(chat_id, f"🏁 <b>ФИНИШ!</b>\n\n🏆 Победитель: <b>{winner_name}</b>!\n\n💰 Банк: {total_bank} монет\n📉 Комиссия бота: {commission} монет\n🎁 Выигрыш: {prize} монет", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
        except Exception as e:
            logger.error(f"Ошибка отправки финала: {e}")
        if chat_id in active_races:
            del active_races[chat_id]
    except Exception as e:
        logger.error(f"Критическая ошибка в race_animation: {e}")
        if chat_id in active_races:
            del active_races[chat_id]

async def team_battle_animation(chat_id):
    try:
        battle = active_battles.get(chat_id)
        if not battle: return
        red_team = battle["red_team"]
        blue_team = battle["blue_team"]
        bet = battle["bet"]
        red_score = 0
        blue_score = 0
        total_rounds = 5
        red_names = [red_team[uid] for uid in red_team]
        blue_names = [blue_team[uid] for uid in blue_team]
        start_msg = f"⚔️ <b>КОМАНДНАЯ БИТВА НАЧИНАЕТСЯ!</b> ⚔️\n\n <b>Красная команда:</b> {', '.join(red_names)}\n🔵 <b>Синяя команда:</b> {', '.join(blue_names)}\n\n Раундов: {total_rounds}\n💰 Банк: {(len(red_team) + len(blue_team)) * bet} монет\n\n Битва начинается..."
        try:
            await bot.send_message(chat_id, start_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки старта битвы: {e}")
        await asyncio.sleep(2)
        for round_num in range(1, total_rounds + 1):
            await asyncio.sleep(1)
            red_player_id = random.choice(list(red_team.keys()))
            blue_player_id = random.choice(list(blue_team.keys()))
            red_name = red_team[red_player_id]
            blue_name = blue_team[blue_player_id]
            red_roll = random.randint(1, 6)
            blue_roll = random.randint(1, 6)
            attempts = 0
            while red_roll == blue_roll and attempts < 10:
                red_roll = random.randint(1, 6)
                blue_roll = random.randint(1, 6)
                attempts += 1
            round_msg = f"🎯 <b>Раунд {round_num}/{total_rounds}</b>\n\n"
            round_msg += f" {red_name} выбросил: {red_roll}️⃣\n"
            round_msg += f"🔵 {blue_name} выбросил: {blue_roll}️⃣\n\n"
            if red_roll > blue_roll:
                red_score += 1
                round_msg += f"🏆 <b>Красная команда побеждает в раунде!</b>\n\n"
            else:
                blue_score += 1
                round_msg += f"🏆 <b>Синяя команда побеждает в раунде!</b>\n\n"
            round_msg += f" Счёт: 🔴 {red_score} - {blue_score} 🔵"
            try:
                await bot.send_message(chat_id, round_msg, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Ошибка отправки раунда {round_num}: {e}")
            await asyncio.sleep(1.5)
        total_players = len(red_team) + len(blue_team)
        total_bank = total_players * bet
        commission = int(total_bank * BATTLE_COMMISSION)
        prize = total_bank - commission
        await asyncio.sleep(1)
        if red_score > blue_score:
            winner_team = red_team
            winner_color = "🔴 Красная"
            for uid in winner_team:
                await update_balance(uid, prize // len(winner_team))
                await update_stats(uid, True)
            for uid in blue_team:
                await update_stats(uid, False)
        elif blue_score > red_score:
            winner_team = blue_team
            winner_color = "🔵 Синяя"
            for uid in winner_team:
                await update_balance(uid, prize // len(winner_team))
                await update_stats(uid, True)
            for uid in red_team:
                await update_stats(uid, False)
        else:
            for uid in red_team:
                await update_balance(uid, bet)
            for uid in blue_team:
                await update_balance(uid, bet)
            winner_color = "🤝 Ничья"
        result_msg = f"🏁 <b>БИТВА ЗАВЕРШЕНА!</b>\n\n📊 Финальный счёт: 🔴 {red_score} - {blue_score} 🔵\n\n"
        if winner_color == " Ничья":
            result_msg += f"🤝 <b>Ничья!</b> Ставки возвращены всем игрокам."
        else:
            result_msg += f"🏆 <b>Победила {winner_color} команда!</b>\n\n"
            result_msg += f" Банк: {total_bank} монет\n"
            result_msg += f"📉 Комиссия бота: {commission} монет\n"
            result_msg += f"🎁 Каждый победитель получает: {prize // len(winner_team)} монет"
        try:
            await bot.send_message(chat_id, result_msg, parse_mode="HTML", reply_markup=back_to_menu_keyboard())
        except Exception as e:
            logger.error(f"Ошибка отправки финала битвы: {e}")
        if chat_id in active_battles:
            del active_battles[chat_id]
    except Exception as e:
        logger.error(f"Критическая ошибка в team_battle_animation: {e}")
        if chat_id in active_battles:
            del active_battles[chat_id]

async def wheel_animation(chat_id):
    try:
        wheel = active_wheels.get(chat_id)
        if not wheel:
            return
        participants = wheel["participants"]
        bet = wheel["bet"]
        user_ids = list(participants.keys())
        names = [participants[uid] for uid in user_ids]
        start_msg = (
            f"🎡 <b>КОЛЕСО ФОРТУНЫ КРУТИТСЯ!</b> 🎡\n\n"
            f" Участники:\n" + "\n".join([f"• {name}" for name in names]) + "\n\n"
            f"💰 Банк: {len(participants) * bet} монет\n\n"
            f"⏳ Колесо вращается..."
        )
        try:
            await bot.send_message(chat_id, start_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Ошибка отправки старта колеса: {e}")
        await asyncio.sleep(2)
        spin_messages = [
            "🎡 Колесо замедляется...\n\n➡️ Стрелка указывает на: {name}",
            " Почти остановилось...\n\n➡️ Стрелка указывает на: {name}",
            "🎡 Ещё чуть-чуть...\n\n➡️ Стрелка указывает на: {name}",
            "🎡 Останавливается...\n\n➡️ Стрелка указывает на: {name}",
        ]
        for msg_template in spin_messages:
            await asyncio.sleep(1.5)
            random_name = random.choice(names)
            try:
                await bot.send_message(chat_id, msg_template.format(name=random_name), parse_mode="HTML")
            except Exception as e:
                logger.error(f"Ошибка отправки анимации колеса: {e}")
        await asyncio.sleep(2)
        winner_id = random.choice(user_ids)
        winner_name = participants[winner_id]
        total_bank = len(participants) * bet
        commission = int(total_bank * WHEEL_COMMISSION)
        prize = total_bank - commission
        await update_balance(winner_id, prize)
        await update_stats(winner_id, True)
        for uid in user_ids:
            if uid != winner_id:
                await update_stats(uid, False)
        final_msg = (
            f"🎉 <b>КОЛЕСО ОСТАНОВИЛОСЬ!</b> 🎉\n\n"
            f"🏆 <b>Победитель: {winner_name}!</b>\n\n"
            f"💰 Банк: {total_bank} монет\n"
            f" Комиссия бота: {commission} монет\n"
            f"🎁 Выигрыш: {prize} монет"
        )
        try:
            await bot.send_message(chat_id, final_msg, parse_mode="HTML", reply_markup=back_to_menu_keyboard())
        except Exception as e:
            logger.error(f"Ошибка отправки финала колеса: {e}")
        if chat_id in active_wheels:
            del active_wheels[chat_id]
    except Exception as e:
        logger.error(f"Критическая ошибка в wheel_animation: {e}")
        if chat_id in active_wheels:
            del active_wheels[chat_id]

async def start_game(user_id, game_type, bet_amount, message):
    if game_type == "coin":
        await safe_send(message, f"🪙 Игра: Монетка\n💰 Ставка: {bet_amount} монет\n\nВыберите сторону:", reply_markup=coin_keyboard())
    elif game_type == "dice":
        await safe_send(message, f"🎲 Игра: Кости\n💰 Ставка: {bet_amount} монет\n\nВыберите число (1-6):", reply_markup=dice_keyboard())
    elif game_type == "roulette":
        await safe_send(message, f"🎯 Игра: Рулетка\n Ставка: {bet_amount} монет\n\nВыберите ставку:", reply_markup=roulette_keyboard())
    elif game_type == "slots":
        await safe_send(message, f"🎰 Игра: Слоты\n💰 Ставка: {bet_amount} монет\n\nСимволы: 🍒  🍇 💎 7️\n\n🎯 3 одинаковых = x10\n✨ 2 одинаковых = x2\n\nНажмите кнопку, чтобы крутить!", reply_markup=slots_keyboard())
    elif game_type == "blackjack":
        deck = create_deck()
        player_hand = [deck.pop(), deck.pop()]
        dealer_hand = [deck.pop(), deck.pop()]
        player_bets[user_id].update({"deck": deck, "player_hand": player_hand, "dealer_hand": dealer_hand})
        player_score = calculate_score(player_hand)
        if player_score == 21:
            while calculate_score(dealer_hand) < 17: dealer_hand.append(deck.pop())
            dealer_score = calculate_score(dealer_hand)
            win_amount = int(bet_amount * 2.5)
            await update_balance(user_id, win_amount)
            await update_stats(user_id, True)
            user = await get_user(user_id)
            await safe_send(message, f" <b>БЛЭКДЖЕК!</b>\n\nВаши карты: {format_hand(player_hand)} ({player_score})\nКарты дилера: {format_hand(dealer_hand)} ({dealer_score})\n\n🎉 Вы выиграли {win_amount} монет (x2.5)!\n💰 Баланс: {user[2]}", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
            if user_id in player_bets: del player_bets[user_id]
        else:
            await safe_send(message, f"🃏 <b>Блэкджек</b>\n Ставка: {bet_amount} монет\n\nВаши карты: {format_hand(player_hand)} ({player_score})\nКарты дилера: {dealer_hand[0]} и [скрыта]\n\nВыберите действие:", parse_mode="HTML", reply_markup=blackjack_keyboard())
    elif game_type == "crash":
        player_bets[user_id]["crash_multiplier"] = 1.00
        player_bets[user_id]["crash_cashed_out"] = False
        player_bets[user_id]["crash_crashed"] = False
        crash_point = round(random.uniform(1.00, 10.00), 2)
        player_bets[user_id]["crash_point"] = crash_point
        await safe_send(message, f"🚀 <b>КРЭШ</b>\n\n💰 Ставка: {bet_amount} монет\n\nМножитель растёт! Нажми <b>ЗАБРАТЬ!</b> до взрыва!\n\nТекущий множитель: <b>x1.00</b>\n\n⚠️ Ракета может взорваться в любой момент!", parse_mode="HTML", reply_markup=crash_keyboard())
        asyncio.create_task(crash_growth(user_id, message))
    elif game_type == "mines":
        player_bets[user_id] = {"game": "mines", "bet": bet_amount}
        await safe_send(message, f"💣 <b>МИННОЕ ПОЛЕ</b>\n\n💰 Ставка: {bet_amount} монет\n\nВыберите количество мин:\n\n3 мины — легко, множитель x1.13-x5.00\n5 мин — средне, множитель x1.23-x15.00\n10 мин — сложно, множитель x1.55-x100.00", parse_mode="HTML", reply_markup=mines_count_keyboard())
        return
    elif game_type == "wheel":
        await safe_send(message, f" <b>КОЛЕСО ФОРТУНЫ</b>\n\nЭта игра работает только в группах!\n\n1. Напишите /wheel <сумма>\n2. Дождитесь других игроков (/join)\n3. Напишите /wheelstart\n\nКаждый игрок = сектор на колесе!\nПобедитель забирает весь банк!", reply_markup=back_to_menu_keyboard())
        await update_balance(user_id, bet_amount)
        if user_id in player_bets: del player_bets[user_id]
        return
    elif game_type == "survival":
        await safe_send(message, f"💀 <b>Кости на выживание</b>\n\nЭта игра работает только в группах!\n\n1. Напишите /survival СУММА\n2. Дождитесь других игроков\n3. Напишите /survivalstart\n\nПоследний выживший забирает банк!", reply_markup=back_to_menu_keyboard())
        await update_balance(user_id, bet_amount)
        if user_id in player_bets: del player_bets[user_id]
    else:
        await update_balance(user_id, bet_amount)
        if user_id in player_bets: del player_bets[user_id]
        await safe_send(message, f"🚧 Эта игра пока в разработке!\n\nСтавка {bet_amount} монет возвращена.", reply_markup=back_to_menu_keyboard())

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await create_user(message.from_user.id, message.from_user.username)
        await safe_send(message, "🎉 Добро пожаловать в Casino Bot!\n\nВам начислено 1000 💰 стартовых монет!\n\nИспользуйте меню ниже для навигации:", reply_markup=main_menu_keyboard())
    else:
        if message.from_user.username: await update_username(message.from_user.id, message.from_user.username)
        await safe_send(message, f"👋 С возвращением, {message.from_user.first_name}!\n\nИспользуйте меню ниже для навигации:", reply_markup=main_menu_keyboard())

@dp.message(Command("balance"))
async def cmd_balance(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await safe_send(message, "❌ Вы еще не зарегистрированы! Напишите /start")
        return
    await safe_send(message, f"💰 Ваш баланс: {user[2]} монет\n\n📊 Статистика:\n├ Игр сыграно: {user[5]}\n├ Побед: {user[3]}\n└ Поражений: {user[4]}", reply_markup=back_to_menu_keyboard())

@dp.message(Command("daily"))
async def cmd_daily(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await safe_send(message, "❌ Вы еще не зарегистрированы! Напишите /start")
        return
    if user[6]:
        last_daily = datetime.fromisoformat(user[6])
        if datetime.now() - last_daily < timedelta(days=1):
            next_bonus = last_daily + timedelta(days=1)
            time_left = next_bonus - datetime.now()
            hours = int(time_left.total_seconds() // 3600)
            minutes = int((time_left.total_seconds() % 3600) // 60)
            await safe_send(message, f"⏰ Вы уже получили ежедневный бонус!\n\nСледующий бонус через:\n🕐 {hours} ч. {minutes} мин.", reply_markup=back_to_menu_keyboard())
            return
    await update_balance(message.from_user.id, DAILY_BONUS)
    await update_daily(message.from_user.id)
    await safe_send(message, f"🎁 Вы получили ежедневный бонус!\n\n+{DAILY_BONUS}  монет\nНовый баланс: {user[2] + DAILY_BONUS} 💰", reply_markup=back_to_menu_keyboard())

@dp.message(Command("games"))
async def cmd_games(message: types.Message):
    await safe_send(message, "🎮 Выберите категорию игр:", reply_markup=main_menu_keyboard())

@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    top_users = await get_top_users(10)
    if not top_users:
        await safe_send(message, " Топ игроков пока пуст!", reply_markup=back_to_menu_keyboard())
        return
    text = " Топ-10 игроков:\n\n" + "".join([f"{i}. @{(user[0] if user[0] else 'Игрок')} - {user[1]} 💰\n" for i, user in enumerate(top_users, 1)])
    await safe_send(message, text, reply_markup=back_to_menu_keyboard())

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user = await get_user(message.from_user.id)
    if not user:
        await safe_send(message, " Вы еще не зарегистрированы! Напишите /start")
        return
    win_rate = (user[3] / user[5] * 100) if user[5] > 0 else 0
    await safe_send(message, f"📊 Ваша статистика:\n\n💰 Баланс: {user[2]} монет\n🎮 Игр сыграно: {user[5]}\n✅ Побед: {user[3]}\n Поражений: {user[4]}\n Процент побед: {win_rate:.1f}%", reply_markup=back_to_menu_keyboard())

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = ("📜 <b>Список команд бота:</b>\n\n👤 <b>Основные:</b>\n/start - Запустить бота\n/balance - Проверить баланс\n/daily - Ежедневный бонус\n/games - Меню игр\n/top - Топ игроков\n/stats - Моя статистика\n/promo КОД - Активировать промокод\n/gift @username СУММА - Подарить монеты\n\n👥 <b>Групповые игры:</b>\n/duel @username СУММА - Дуэль 1 на 1\n/survival СУММА - Выживание\n/survivalstart - Начать выживание\n/jackpot СУММА СЕКУНДЫ - Джекпот\n/race СУММА - Гонки\n/racestart - Начать гонку\n/battle СУММА - Командная битва\n/battlestart - Начать битву\n/wheel СУММА - Колесо Фортуны\n/wheelstart - Начать вращение\n/join - Вступить в игру\n\n🛠 <b>Администратор:</b>\n/admin - Админ-панель\n/users - Статистика пользователей\n/createpromo КОД СУММА - Создать промокод\n/broadcast - Сделать рассылку\n\n⚠️ <b>Важно:</b> В одной группе одновременно может идти не более 2 игр. Если в чате уже идут игры — дождитесь их окончания!")
    await safe_send(message, text, parse_mode="HTML", reply_markup=back_to_menu_keyboard())

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await safe_send(message, "⛔ У вас нет прав администратора!")
        return
    await safe_send(message, "🛠 <b>Админ-панель</b>\n\nВыберите действие:", parse_mode="HTML", reply_markup=admin_menu_keyboard())

@dp.message(Command("users"))
async def cmd_users(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await safe_send(message, "⛔ У вас нет прав администратора!")
        return
    stats = await get_users_count()
    win_rate = (stats['total_wins'] / stats['total_games'] * 100) if stats['total_games'] > 0 else 0
    text = (f"📊 <b>Статистика пользователей</b>\n\n👥 <b>Всего пользователей:</b> {stats['total']}\n✅ Активных: {stats['active']}\n🚫 Заблокированных: {stats['banned']}\n\n💰 <b>Всего монет в экономике:</b> {stats['total_balance']}\n\n🎮 <b>Всего игр сыграно:</b> {stats['total_games']}\n✅ Всего побед: {stats['total_wins']}\n Процент побед: {win_rate:.1f}%")
    await safe_send(message, text, parse_mode="HTML")

@dp.message(Command("broadcast"))
async def cmd_broadcast_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await safe_send(message, "⛔ У вас нет прав администратора!")
        return
    broadcast_users.add(message.from_user.id)
    await safe_send(message, "📡 <b>Режим рассылки активирован!</b>\n\nПришлите текст, фото, видео или любой другой файл.\nЧтобы отменить, напишите /cancel", parse_mode="HTML")

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    user_id = message.from_user.id
    if user_id in broadcast_users:
        broadcast_users.remove(user_id)
        await safe_send(message, "❌ Рассылка отменена.")
    if user_id in waiting_for_bet:
        del waiting_for_bet[user_id]
        await safe_send(message, "❌ Ввод ставки отменён.", reply_markup=back_to_menu_keyboard())

@dp.message(lambda m: m.from_user.id in broadcast_users)
async def process_broadcast(message: types.Message):
    user_id = message.from_user.id
    if user_id not in broadcast_users: return
    broadcast_users.remove(user_id)
    users = await get_all_users()
    sent = 0
    failed = 0
    await safe_send(message, f"📡 Начинаю рассылку для {len(users)} пользователей...")
    for (uid,) in users:
        try:
            await message.copy_to(uid)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await safe_send(message, f"✅ <b>Рассылка завершена!</b>\n\n📬 Доставлено: {sent}\n❌ Ошибок: {failed}", parse_mode="HTML")

@dp.message(lambda m: m.from_user.id in waiting_for_bet)
async def process_custom_bet_input(message: types.Message):
    user_id = message.from_user.id
    if user_id not in waiting_for_bet: return
    bet_data = waiting_for_bet[user_id]
    if (datetime.now() - bet_data["time"]).total_seconds() > BET_TIMEOUT:
        del waiting_for_bet[user_id]
        await safe_send(message, " Время на ввод ставки истекло. Попробуйте ещё раз.", reply_markup=back_to_menu_keyboard())
        return
    if message.text and message.text.startswith('/cancel'):
        del waiting_for_bet[user_id]
        await safe_send(message, "❌ Ввод ставки отменён.", reply_markup=back_to_menu_keyboard())
        return
    if not message.text:
        await safe_send(message, "⚠️ Пожалуйста, введите число текстом.\nДля отмены напишите /cancel")
        return
    game_type = bet_data["game"]
    clean_text = message.text.strip().replace(" ", "").replace(",", ".")
    try:
        bet_amount = int(float(clean_text))
    except ValueError:
        await safe_send(message, "️ Введите только число! Например: 5000\nДля отмены напишите /cancel")
        return
    if bet_amount <= 0:
        await safe_send(message, "⚠️ Ставка должна быть больше нуля!\nДля отмены напишите /cancel")
        return
    user = await get_user(user_id)
    if user[2] < bet_amount:
        await safe_send(message, f"❌ Недостаточно монет!\n\nВаш баланс: {user[2]} 💰\nНеобходимо: {bet_amount} 💰\nДля отмены напишите /cancel")
        del waiting_for_bet[user_id]
        return
    del waiting_for_bet[user_id]
    await update_balance(user_id, -bet_amount)
    player_bets[user_id]["bet"] = bet_amount
    await start_game(user_id, game_type, bet_amount, message)

@dp.message(Command("jackpot"))
async def cmd_jackpot(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await safe_send(message, "🚫 Джекпот работает только в группах!")
        return
    if get_active_sessions(message.chat.id) >= MAX_GROUP_SESSIONS:
        await safe_send(message, f"⚠️ В этой группе уже идёт {MAX_GROUP_SESSIONS} игры одновременно!\n\nПодождите, пока одна из них закончится, и попробуйте снова.\n\nЭто ограничение нужно для стабильной работы бота.", reply_markup=back_to_menu_keyboard())
        return
    if message.chat.id in active_jackpots:
        await safe_send(message, " В этом чате уже идет Джекпот! Дождитесь окончания.")
        return
    args = message.text.split()
    if len(args) != 3:
        await safe_send(message, "Использование: /jackpot <ставка> <секунды>\nПример: /jackpot 100 300")
        return
    try:
        bet = int(args[1])
        seconds = int(args[2])
    except ValueError:
        await safe_send(message, "⚠️ Ставка и время должны быть числами!")
        return
    if bet <= 0 or seconds <= 0:
        await safe_send(message, "️ Ставка и время должны быть больше нуля!")
        return
    if seconds > 3600:
        await safe_send(message, "️ Максимальное время таймера - 3600 секунд (1 час).")
        return
    user = await get_user(message.from_user.id)
    if not user or user[2] < bet:
        await safe_send(message, f"❌ Недостаточно монет! Нужно: {bet} 💰")
        return
    await update_balance(message.from_user.id, -bet)
    active_jackpots[message.chat.id] = {"bet": bet, "end_time": datetime.now() + timedelta(seconds=seconds), "participants": {message.from_user.id: message.from_user.first_name}, "creator": message.from_user.id, "chat_id": message.chat.id}
    await safe_send(message, f"🎰 <b>ДЖЕКПОТ СОЗДАН!</b>\n\n💰 Взнос: {bet} монет\n⏰ Розыгрыш через: {seconds} секунд\n👥 Участников: 1\n\nДля участия напишите /join", parse_mode="HTML")
    asyncio.create_task(jackpot_timer(message.chat.id))

@dp.message(Command("race"))
async def cmd_race(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await safe_send(message, "🚫 Гонки работают только в группах!")
        return
    if get_active_sessions(message.chat.id) >= MAX_GROUP_SESSIONS:
        await safe_send(message, f"⚠️ В этой группе уже идёт {MAX_GROUP_SESSIONS} игры одновременно!\n\nПодождите, пока одна из них закончится, и попробуйте снова.\n\nЭто ограничение нужно для стабильной работы бота.", reply_markup=back_to_menu_keyboard())
        return
    if message.chat.id in active_races:
        await safe_send(message, "⏳ В этом чате уже идет гонка! Дождитесь окончания.")
        return
    args = message.text.split()
    if len(args) != 2:
        await safe_send(message, "Использование: /race <ставка>\nПример: /race 100")
        return
    try:
        bet = int(args[1])
    except ValueError:
        await safe_send(message, "⚠️ Ставка должна быть числом!")
        return
    if bet <= 0:
        await safe_send(message, "⚠️ Ставка должна быть больше нуля!")
        return
    user = await get_user(message.from_user.id)
    if not user or user[2] < bet:
        await safe_send(message, f"❌ Недостаточно монет! Нужно: {bet} 💰")
        return
    await update_balance(message.from_user.id, -bet)
    active_races[message.chat.id] = {"bet": bet, "participants": {message.from_user.id: message.from_user.first_name}, "creator": message.from_user.id, "chat_id": message.chat.id}
    await safe_send(message, f"🏇 <b>ГОНКА СОЗДАНА!</b>\n\n💰 Взнос: {bet} монет\n👥 Участников: 1\n\nДля участия напишите /join\nКогда наберётся 2+ участников, напишите /racestart", parse_mode="HTML")

@dp.message(Command("racestart"))
async def cmd_racestart(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await safe_send(message, "🚫 Гонки работают только в группах!")
        return
    if message.chat.id not in active_races:
        await safe_send(message, "❌ В этом чате нет активной гонки!")
        return
    race = active_races[message.chat.id]
    if message.from_user.id != race["creator"]:
        await safe_send(message, "🚫 Начать гонку может только её создатель!")
        return
    if len(race["participants"]) < 2:
        await safe_send(message, "❌ Нужно минимум 2 участника для начала гонки!")
        return
    await safe_send(message, f" <b>ГОНКА НАЧИНАЕТСЯ!</b>\n\n👥 Участники ({len(race['participants'])}):\n" + "\n".join([f"• {name}" for name in race["participants"].values()]) + f"\n\n💰 Банк: {len(race['participants']) * race['bet']} монет", parse_mode="HTML")
    asyncio.create_task(race_animation(message.chat.id))

@dp.message(Command("battle"))
async def cmd_battle(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await safe_send(message, "🚫 Командная битва работает только в группах!")
        return
    if get_active_sessions(message.chat.id) >= MAX_GROUP_SESSIONS:
        await safe_send(message, f"⚠️ В этой группе уже идёт {MAX_GROUP_SESSIONS} игры одновременно!\n\nПодождите, пока одна из них закончится, и попробуйте снова.\n\nЭто ограничение нужно для стабильной работы бота.", reply_markup=back_to_menu_keyboard())
        return
    if message.chat.id in active_battles:
        await safe_send(message, "⏳ В этом чате уже идет битва! Дождитесь окончания.")
        return
    args = message.text.split()
    if len(args) != 2:
        await safe_send(message, "Использование: /battle <ставка>\nПример: /battle 100")
        return
    try:
        bet = int(args[1])
    except ValueError:
        await safe_send(message, "⚠️ Ставка должна быть числом!")
        return
    if bet <= 0:
        await safe_send(message, "⚠️ Ставка должна быть больше нуля!")
        return
    user = await get_user(message.from_user.id)
    if not user or user[2] < bet:
        await safe_send(message, f"❌ Недостаточно монет! Нужно: {bet} 💰")
        return
    await update_balance(message.from_user.id, -bet)
    active_battles[message.chat.id] = {"bet": bet, "red_team": {message.from_user.id: message.from_user.first_name}, "blue_team": {}, "creator": message.from_user.id, "chat_id": message.chat.id}
    await safe_send(message, f"⚔️ <b>КОМАНДНАЯ БИТВА СОЗДАНА!</b>\n\n💰 Взнос: {bet} монет\n🔴 Красная команда: {message.from_user.first_name}\n Синяя команда: (ожидание)\n\nДля участия напишите /join\nМинимум 4 игрока (2 в каждой команде)\nКогда наберётся 4+ участников, напишите /battlestart", parse_mode="HTML")

@dp.message(Command("battlestart"))
async def cmd_battlestart(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await safe_send(message, "🚫 Командная битва работает только в группах!")
        return
    if message.chat.id not in active_battles:
        await safe_send(message, "❌ В этом чате нет активной битвы!")
        return
    battle = active_battles[message.chat.id]
    if message.from_user.id != battle["creator"]:
        await safe_send(message, "🚫 Начать битву может только её создатель!")
        return
    if len(battle["red_team"]) < 2 or len(battle["blue_team"]) < 2:
        await safe_send(message, "❌ Нужно минимум 2 игрока в каждой команде (всего 4)!")
        return
    red_names = list(battle["red_team"].values())
    blue_names = list(battle["blue_team"].values())
    total_players = len(battle["red_team"]) + len(battle["blue_team"])
    await safe_send(message, f"⚔️ <b>КОМАНДНАЯ БИТВА НАЧИНАЕТСЯ!</b>\n\n🔴 <b>Красная команда:</b> {', '.join(red_names)}\n🔵 <b>Синяя команда:</b> {', '.join(blue_names)}\n\n🎯 Раундов: 5\n💰 Банк: {total_players * battle['bet']} монет", parse_mode="HTML")
    asyncio.create_task(team_battle_animation(message.chat.id))

@dp.message(Command("wheel"))
async def cmd_wheel(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await safe_send(message, "🚫 Колесо Фортуны работает только в группах!")
        return
    if get_active_sessions(message.chat.id) >= MAX_GROUP_SESSIONS:
        await safe_send(message, f"️ В этой группе уже идёт {MAX_GROUP_SESSIONS} игры одновременно!\n\nПодождите, пока одна из них закончится, и попробуйте снова.\n\nЭто ограничение нужно для стабильной работы бота.", reply_markup=back_to_menu_keyboard())
        return
    if message.chat.id in active_wheels:
        await safe_send(message, "⏳ В этом чате уже крутится Колесо! Дождитесь окончания.")
        return
    args = message.text.split()
    if len(args) != 2:
        await safe_send(message, "Использование: /wheel <ставка>\nПример: /wheel 100")
        return
    try:
        bet = int(args[1])
    except ValueError:
        await safe_send(message, "️ Ставка должна быть числом!")
        return
    if bet <= 0:
        await safe_send(message, "⚠️ Ставка должна быть больше нуля!")
        return
    user = await get_user(message.from_user.id)
    if not user or user[2] < bet:
        await safe_send(message, f"❌ Недостаточно монет! Нужно: {bet} 💰")
        return
    await update_balance(message.from_user.id, -bet)
    active_wheels[message.chat.id] = {"bet": bet, "participants": {message.from_user.id: message.from_user.first_name}, "creator": message.from_user.id, "chat_id": message.chat.id}
    await safe_send(message, f"🎡 <b>КОЛЕСО ФОРТУНЫ СОЗДАНО!</b>\n\n💰 Взнос: {bet} монет\n👥 Участников: 1\n\nДля участия напишите /join\nКогда наберётся 2+ участников, напишите /wheelstart", parse_mode="HTML")

@dp.message(Command("wheelstart"))
async def cmd_wheelstart(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await safe_send(message, "🚫 Колесо Фортуны работает только в группах!")
        return
    if message.chat.id not in active_wheels:
        await safe_send(message, "❌ В этом чате нет активного Колеса!")
        return
    wheel = active_wheels[message.chat.id]
    if message.from_user.id != wheel["creator"]:
        await safe_send(message, "🚫 Начать вращение может только создатель!")
        return
    if len(wheel["participants"]) < 2:
        await safe_send(message, "❌ Нужно минимум 2 участника для вращения!")
        return
    names = list(wheel["participants"].values())
    total_bank = len(wheel["participants"]) * wheel["bet"]
    await safe_send(message, f"🎡 <b>КОЛЕСО ФОРТУНЫ ГОТОВО К ВРАЩЕНИЮ!</b>\n\n👥 Сектора ({len(names)}):\n" + "\n".join([f"• {name}" for name in names]) + f"\n\n💰 Банк: {total_bank} монет\n\n⏳ Колесо начинает вращаться...", parse_mode="HTML")
    asyncio.create_task(wheel_animation(message.chat.id))

@dp.message(Command("join"))
async def cmd_join(message: types.Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    if chat_id in active_jackpots:
        jp = active_jackpots[chat_id]
        if user_id in jp["participants"]:
            await safe_send(message, "⚠️ Вы уже участвуете в этом Джекпоте!")
            return
        user = await get_user(user_id)
        if not user or user[2] < jp["bet"]:
            await safe_send(message, f"❌ Недостаточно монет! Нужно: {jp['bet']} 💰")
            return
        await update_balance(user_id, -jp["bet"])
        jp["participants"][user_id] = user_name
        await safe_send(message, f"✅ <b>{user_name}</b> вступает в Джекпот!\n👥 Всего участников: {len(jp['participants'])}")
        return
    if chat_id in active_races:
        race = active_races[chat_id]
        if user_id in race["participants"]:
            await safe_send(message, "⚠️ Вы уже участвуете в этой гонке!")
            return
        user = await get_user(user_id)
        if not user or user[2] < race["bet"]:
            await safe_send(message, f"❌ Недостаточно монет! Нужно: {race['bet']} ")
            return
        await update_balance(user_id, -race["bet"])
        race["participants"][user_id] = user_name
        await safe_send(message, f"✅ <b>{user_name}</b> вступает в гонку!\n👥 Всего участников: {len(race['participants'])}")
        return
    if chat_id in active_battles:
        battle = active_battles[chat_id]
        if user_id in battle["red_team"] or user_id in battle["blue_team"]:
            await safe_send(message, "⚠️ Вы уже участвуете в этой битве!")
            return
        user = await get_user(user_id)
        if not user or user[2] < battle["bet"]:
            await safe_send(message, f"❌ Недостаточно монет! Нужно: {battle['bet']} 💰")
            return
        if len(battle["red_team"]) <= len(battle["blue_team"]):
            battle["red_team"][user_id] = user_name
            team_color = "🔴 Красную"
        else:
            battle["blue_team"][user_id] = user_name
            team_color = "🔵 Синюю"
        await update_balance(user_id, -battle["bet"])
        await safe_send(message, f"✅ <b>{user_name}</b> вступает в {team_color} команду!\n🔴 Красных: {len(battle['red_team'])}\n🔵 Синих: {len(battle['blue_team'])}")
        return
    if chat_id in active_wheels:
        wheel = active_wheels[chat_id]
        if user_id in wheel["participants"]:
            await safe_send(message, "⚠️ Вы уже участвуете в этом Колесе!")
            return
        user = await get_user(user_id)
        if not user or user[2] < wheel["bet"]:
            await safe_send(message, f"❌ Недостаточно монет! Нужно: {wheel['bet']} 💰")
            return
        await update_balance(user_id, -wheel["bet"])
        wheel["participants"][user_id] = user_name
        await safe_send(message, f"✅ <b>{user_name}</b> становится сектором Колеса!\n👥 Всего секторов: {len(wheel['participants'])}")
        return
    await safe_send(message, "❌ Сейчас нет активной игры в этом чате!")

@dp.message(Command("gift"))
async def cmd_gift(message: types.Message):
    args = message.text.split()
    if len(args) != 3:
        await safe_send(message, "Использование: /gift @username СУММА\nПример: /gift @durov 500")
        return
    target_username = args[1].lower().replace("@", "")
    try: amount = int(args[2])
    except ValueError:
        await safe_send(message, "⚠️ Сумма должна быть числом!")
        return
    if amount <= 0:
        await safe_send(message, "⚠️ Сумма должна быть больше нуля!")
        return
    sender = await get_user(message.from_user.id)
    if sender[2] < amount:
        await safe_send(message, f"❌ Недостаточно монет! Ваш баланс: {sender[2]}")
        return
    recipient = await get_user_by_username(target_username)
    if not recipient:
        await safe_send(message, f"❌ Пользователь @{target_username} не найден!\n\nУбедитесь, что:\n1. Он уже запускал этого бота (/start)\n2. У него установлен username в Telegram")
        return
    if recipient[0] == message.from_user.id:
        await safe_send(message, "🚫 Нельзя дарить монеты самому себе!")
        return
    await update_balance(message.from_user.id, -amount)
    await update_balance(recipient[0], amount)
    await create_gift(message.from_user.id, recipient[0], amount)
    await safe_send(message, f"✅ Вы успешно перевели {amount} 💰 пользователю @{target_username}!\nВаш новый баланс: {sender[2] - amount}")
    await safe_send_to_user(recipient[0], f"🎁 Вам перевели {amount} 💰 от пользователя {message.from_user.first_name}!")

@dp.message(Command("duel"))
async def cmd_duel(message: types.Message):
    args = message.text.split()
    if len(args) != 3:
        await safe_send(message, "⚔️ <b>Дуэль на костях</b>\n\nИспользование: /duel @username СУММА\nПример: /duel @durov 100\n\n⚠️ Второй игрок должен принять вызов!", parse_mode="HTML")
        return
    opponent_username = args[1].lower().replace("@", "")
    try: bet_amount = int(args[2])
    except ValueError:
        await safe_send(message, "⚠️ Сумма должна быть числом!")
        return
    if bet_amount <= 0:
        await safe_send(message, "⚠️ Сумма должна быть больше нуля!")
        return
    challenger = await get_user(message.from_user.id)
    if not challenger:
        await safe_send(message, "❌ Сначала запустите бота командой /start!")
        return
    if challenger[2] < bet_amount:
        await safe_send(message, f"❌ Недостаточно монет! Ваш баланс: {challenger[2]} 💰")
        return
    if message.from_user.username and message.from_user.username.lower() == opponent_username:
        await safe_send(message, "🚫 Нельзя вызывать на дуэль самого себя!")
        return
    opponent = await get_user_by_username(opponent_username)
    if not opponent:
        await safe_send(message, f" Игрок @{opponent_username} не найден!\n\nУбедитесь, что:\n1. Он уже запускал этого бота (/start)\n2. У него установлен username в Telegram")
        return
    if opponent[2] < bet_amount:
        await safe_send(message, f"❌ У игрока @{opponent_username} недостаточно монет!")
        return
    duel_key = f"{message.from_user.id}_{opponent[0]}"
    if duel_key in pending_duels:
        await safe_send(message, "⏳ Вы уже вызвали этого игрока на дуэль! Ждите ответа.")
        return
    await update_balance(message.from_user.id, -bet_amount)
    challenger_name = message.from_user.first_name
    opponent_name = opponent[1] if opponent[1] else f"ID:{opponent[0]}"
    pending_duels[duel_key] = {"bet": bet_amount, "challenger_id": message.from_user.id, "opponent_id": opponent[0], "challenger_name": challenger_name, "opponent_name": opponent_name, "chat_id": message.chat.id}
    await safe_send(message, f"⚔️ Вызов отправлен игроку @{opponent_username}!\n💰 Ваша ставка {bet_amount} 💰 заморожена.\n\nЖдём подтверждения...", reply_markup=back_to_menu_keyboard())
    result = await safe_send_to_user(opponent[0], f"⚔️ <b>ВЫЗОВ НА ДУЭЛЬ!</b>\n\n {challenger_name} вызывает вас на дуэль!\n💰 Ставка: {bet_amount} монет\n🏆 Приз: {int(bet_amount * 2 * (1 - DUEL_COMMISSION))} монет\n\nПримите или отклоните вызов:", parse_mode="HTML", reply_markup=duel_response_keyboard(message.from_user.id, opponent[0]))
    if not result:
        await update_balance(message.from_user.id, bet_amount)
        del pending_duels[duel_key]
        await safe_send(message, "❌ Не удалось отправить вызов. Ставка возвращена.")

@dp.callback_query(lambda c: c.data.startswith("duel_accept_"))
async def accept_duel(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    challenger_id, opponent_id = int(parts[2]), int(parts[3])
    if callback.from_user.id != opponent_id:
        await callback.answer("❌ Это не ваш вызов!", show_alert=True)
        return
    duel_key = f"{challenger_id}_{opponent_id}"
    if duel_key not in pending_duels:
        await safe_send(callback.message, "⏳ Эта дуэль уже завершена или отменена.")
        await callback.answer()
        return
    duel = pending_duels[duel_key]
    bet = duel["bet"]
    opponent = await get_user(opponent_id)
    if opponent[2] < bet:
        await safe_send(callback.message, f" У вас недостаточно монет для дуэли! Нужно: {bet} 💰")
        del pending_duels[duel_key]
        await update_balance(challenger_id, bet)
        await safe_send_to_user(challenger_id, "❌ Дуэль отменена: у противника недостаточно монет. Ставка возвращена.")
        await callback.answer()
        return
    await update_balance(opponent_id, -bet)
    bank = bet * 2
    commission = int(bank * DUEL_COMMISSION)
    prize = bank - commission
    challenger_roll, opponent_roll = random.randint(1, 6), random.randint(1, 6)
    challenger_name, opponent_name = duel["challenger_name"], duel["opponent_name"]
    if challenger_roll > opponent_roll:
        await update_balance(challenger_id, prize)
        await update_stats(challenger_id, True)
        await update_stats(opponent_id, False)
        result_text = f"⚔️ <b>ДУЭЛЬ НА КОСТЯХ!</b>\n\n🎲 {challenger_name} выбросил: {challenger_roll}️⃣\n🎲 {opponent_name} выбросил: {opponent_roll}️⃣\n\n🏆 <b>Победил {challenger_name}!</b>\n\n💰 Банк: {bank} монет\n📉 Комиссия бота: {commission} монет\n Выигрыш: {prize} монет"
    elif opponent_roll > challenger_roll:
        await update_balance(opponent_id, prize)
        await update_stats(opponent_id, True)
        await update_stats(challenger_id, False)
        result_text = f"⚔️ <b>ДУЭЛЬ НА КОСТЯХ!</b>\n\n {challenger_name} выбросил: {challenger_roll}️⃣\n {opponent_name} выбросил: {opponent_roll}️⃣\n\n <b>Победил {opponent_name}!</b>\n\n💰 Банк: {bank} монет\n📉 Комиссия бота: {commission} монет\n🎁 Выигрыш: {prize} монет"
    else:
        await update_balance(challenger_id, bet)
        await update_balance(opponent_id, bet)
        result_text = f"⚔️ <b>ДУЭЛЬ НА КОСТЯХ!</b>\n\n🎲 {challenger_name} выбросил: {challenger_roll}️⃣\n🎲 {opponent_name} выбросил: {opponent_roll}️\n\n🤝 <b>Ничья!</b> Ставки возвращены обоим игрокам."
    del pending_duels[duel_key]
    await safe_send(callback.message, result_text, parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()
    await safe_send_to_user(challenger_id, result_text, parse_mode="HTML")

@dp.callback_query(lambda c: c.data.startswith("duel_decline_"))
async def decline_duel(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    challenger_id, opponent_id = int(parts[2]), int(parts[3])
    if callback.from_user.id != opponent_id:
        await callback.answer("❌ Это не ваш вызов!", show_alert=True)
        return
    duel_key = f"{challenger_id}_{opponent_id}"
    if duel_key not in pending_duels:
        await safe_send(callback.message, "⏳ Эта дуэль уже завершена или отменена.")
        await callback.answer()
        return
    bet = pending_duels[duel_key]["bet"]
    await update_balance(challenger_id, bet)
    del pending_duels[duel_key]
    await safe_send(callback.message, f"❌ Вы отклонили дуэль.\nСтавка {bet} 💰 возвращена противнику.", reply_markup=back_to_menu_keyboard())
    await callback.answer()
    await safe_send_to_user(challenger_id, f"❌ Игрок отклонил ваш вызов на дуэль.\nСтавка {bet} 💰 возвращена на ваш баланс.", reply_markup=back_to_menu_keyboard())

@dp.message(Command("survival"))
async def cmd_survival(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await safe_send(message, "🚫 Эта игра работает только в группах!")
        return
    if get_active_sessions(message.chat.id) >= MAX_GROUP_SESSIONS:
        await safe_send(message, f"⚠️ В этой группе уже идёт {MAX_GROUP_SESSIONS} игры одновременно!\n\nПодождите, пока одна из них закончится, и попробуйте снова.\n\nЭто ограничение нужно для стабильной работы бота.", reply_markup=back_to_menu_keyboard())
        return
    args = message.text.split()
    if len(args) != 2:
        await safe_send(message, " <b>Кости на выживание</b>\n\nИспользование: /survival СУММА\nПример: /survival 100\n\n🎲 Несколько игроков регистрируются, затем пишут /survivalstart\nКаждый раунд кто выбросил меньше всех — выбывает!\nПоследний выживший забирает банк!", parse_mode="HTML")
        return
    try: bet_amount = int(args[1])
    except ValueError:
        await safe_send(message, "⚠️ Сумма должна быть числом!")
        return
    if bet_amount <= 0:
        await safe_send(message, "⚠️ Сумма должна быть больше нуля!")
        return
    user = await get_user(message.from_user.id)
    if not user:
        await safe_send(message, "❌ Сначала запустите бота командой /start!")
        return
    if user[2] < bet_amount:
        await safe_send(message, f"❌ Недостаточно монет! Ваш баланс: {user[2]} 💰")
        return
    chat_id = message.chat.id
    if chat_id not in survival_games:
        survival_games[chat_id] = {"players": {}, "bet": bet_amount, "creator_id": message.from_user.id, "round": 0, "active": False}
    game = survival_games[chat_id]
    if game["bet"] != bet_amount:
        await safe_send(message, f"️ Ставка в этой игре уже установлена: {game['bet']} 💰")
        return
    if message.from_user.id in game["players"]:
        await safe_send(message, "⚠️ Вы уже зарегистрированы в этой игре!")
        return
    await update_balance(message.from_user.id, -bet_amount)
    username = message.from_user.first_name
    game["players"][message.from_user.id] = username
    players_list = "\n".join([f"• {name}" for name in game["players"].values()])
    await safe_send(message, f"💀 <b>Игра на выживание</b>\n\n✅ {username} зарегистрировался!\n💰 Ставка: {bet_amount} монет\n👥 Игроки ({len(game['players'])}):\n{players_list}\n\nДля начала игры напишите /survivalstart", parse_mode="HTML")

@dp.message(Command("survivalstart"))
async def cmd_start_survival(message: types.Message):
    if message.chat.type not in ["group", "supergroup"]:
        await safe_send(message, "🚫 Эта игра работает только в группах!")
        return
    chat_id = message.chat.id
    if chat_id not in survival_games:
        await safe_send(message, "❌ Сначала игроки должны зарегистрироваться через /survival СУММА")
        return
    game = survival_games[chat_id]
    if len(game["players"]) < 2:
        await safe_send(message, "❌ Нужно минимум 2 игрока для начала игры!")
        return
    if message.from_user.id != game["creator_id"]:
        await safe_send(message, "🚫 Начать игру может только её создатель!")
        return
    game["active"] = True
    game["round"] = 1
    players_list = "\n".join([f"• {name}" for name in game["players"].values()])
    bank = len(game["players"]) * game["bet"]
    prize = int(bank * (1 - SURVIVAL_COMMISSION))
    await safe_send(message, f"💀 <b>ИГРА НА ВЫЖИВАНИЕ НАЧАЛАСЬ!</b>\n\n👥 Игроки:\n{players_list}\n\n💰 Банк: {bank} монет\n🏆 Приз победителя: {prize} монет\n📉 Комиссия бота: {bank - prize} монет\n\n🎲 <b>Раунд 1</b>\n\nНажмите кнопку, чтобы бросить кубик!", parse_mode="HTML", reply_markup=survival_keyboard())

@dp.callback_query(lambda c: c.data == "survival_roll")
async def survival_roll(callback: types.CallbackQuery):
    chat_id = callback.message.chat.id
    if chat_id not in survival_games:
        await callback.answer("❌ Игра не найдена!", show_alert=True)
        return
    game = survival_games[chat_id]
    if not game["active"]:
        await callback.answer("⏳ Игра еще не началась!", show_alert=True)
        return
    user_id = callback.from_user.id
    if user_id not in game["players"]:
        await callback.answer("❌ Вы не участвуете в этой игре!", show_alert=True)
        return
    rolls = {uid: random.randint(1, 6) for uid in game["players"]}
    min_roll = min(rolls.values())
    eliminated = [uid for uid, roll in rolls.items() if roll == min_roll]
    survivors = [uid for uid in game["players"] if uid not in eliminated]
    rolls_text = "".join([f"🎲 {game['players'][uid]}: {roll}️ {'💀 ВЫБЫЛ' if uid in eliminated else '✅ Выжил'}\n" for uid, roll in rolls.items()])
    if len(survivors) == 1:
        winner_id = survivors[0]
        winner_name = game["players"][winner_id]
        bank = len(game["players"]) * game["bet"]
        prize = int(bank * (1 - SURVIVAL_COMMISSION))
        await update_balance(winner_id, prize)
        await update_stats(winner_id, True)
        for uid in eliminated: await update_stats(uid, False)
        result_text = f"💀 <b>ФИНАЛЬНЫЙ РАУНД!</b>\n\n{rolls_text}\n🏆 <b>ПОБЕДИТЕЛЬ: {winner_name}!</b>\n\n💰 Выигрыш: {prize} монет\n💀 Выбыло в этом раунде: {len(eliminated)}"
        del survival_games[chat_id]
        await safe_send(callback.message, result_text, parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    else:
        for uid in eliminated:
            del game["players"][uid]
            await update_stats(uid, False)
        game["round"] += 1
        survivors_list = "\n".join([f"• {game['players'][uid]}" for uid in survivors])
        result_text = f"💀 <b>Раунд {game['round'] - 1} завершен!</b>\n\n{rolls_text}\n💀 Выбыло: {len(eliminated)}\n\n👥 Остались ({len(survivors)}):\n{survivors_list}\n\n🎲 <b>Раунд {game['round']}</b>\n\nНажмите кнопку, чтобы бросить кубик!"
        await safe_send(callback.message, result_text, parse_mode="HTML", reply_markup=survival_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "main_menu")
async def process_main_menu(callback: types.CallbackQuery):
    await safe_send(callback.message, "🏠 Главное меню\n\nВыберите действие:", reply_markup=main_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "solo_games")
async def process_solo_games(callback: types.CallbackQuery):
    await safe_send(callback.message, "🎰 <b>Одиночные игры</b>\n\nИграйте против бота!", reply_markup=solo_games_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "group_games")
async def process_group_games(callback: types.CallbackQuery):
    await safe_send(callback.message, "👥 <b>Групповые игры</b>\n\nИграйте с друзьями в группе!\n\n️ В одной группе одновременно может идти не более 2 игр.\n\nДля групповых игр используйте команды в чате группы.", reply_markup=group_games_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "group_duel")
async def process_group_duel(callback: types.CallbackQuery):
    await safe_send(callback.message, "️ <b>Дуэль на костях (1 на 1)</b>\n\nВызовите другого игрока на дуэль!\n\nВведите в чате группы:\n/duel @username СУММА\n\nПример: /duel @durov 100\n\n⚠️ Второй игрок должен принять вызов!", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "group_survival")
async def process_group_survival(callback: types.CallbackQuery):
    await safe_send(callback.message, "💀 <b>Кости на выживание</b>\n\nНесколько игроков регистрируются, затем пишут /survivalstart\nКаждый раунд кто выбросил меньше всех — выбывает!\nПоследний выживший забирает банк!\n\nВведите в чате группы:\n/survival СУММА\n\nПример: /survival 100", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "group_jackpot")
async def process_group_jackpot(callback: types.CallbackQuery):
    await safe_send(callback.message, " <b>Джекпот</b>\n\nСоздайте джекпот с таймером!\nИгроки вступают, и через указанное время бот случайно выбирает победителя.\n\nВведите в чате группы:\n/jackpot СУММА СЕКУНДЫ\n\nПример: /jackpot 100 300\n\nЗатем другие игроки пишут /join", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "group_race")
async def process_group_race(callback: types.CallbackQuery):
    await safe_send(callback.message, "🏇 <b>Гонки</b>\n\nКаждый игрок становится 'лошадью'!\nБот показывает анимацию гонки, и победитель забирает банк.\n\nВведите в чате группы:\n/race СУММА\n\nПример: /race 100\n\nЗатем другие игроки пишут /join\nКогда наберётся 2+ участников, напишите /racestart", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "group_battle")
async def process_group_battle(callback: types.CallbackQuery):
    await safe_send(callback.message, "⚔️ <b>Командная битва (Red vs Blue)</b>\n\nИгроки автоматически делятся на 2 команды!\nБот проводит 5 раундов дуэлей между командами.\nКоманда-победитель забирает банк.\n\nВведите в чате группы:\n/battle СУММА\n\nПример: /battle 100\n\nЗатем другие игроки пишут /join\nМинимум 4 игрока (2 в каждой команде)\nКогда наберётся 4+ участников, напишите /battlestart", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "group_wheel")
async def process_group_wheel(callback: types.CallbackQuery):
    await safe_send(callback.message, "🎡 <b>Колесо Фортуны</b>\n\nКаждый игрок становится сектором на колесе!\nБот крутит колесо с анимацией, и победитель забирает банк.\n\nВведите в чате группы:\n/wheel СУММА\n\nПример: /wheel 100\n\nЗатем другие игроки пишут /join\nКогда наберётся 2+ участников, напишите /wheelstart", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "balance")
async def process_balance(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await safe_send(callback.message, "❌ Вы еще не зарегистрированы! Напишите /start")
        await callback.answer()
        return
    await safe_send(callback.message, f"💰 Ваш баланс: {user[2]} монет\n\n Статистика:\n├ Игр сыграно: {user[5]}\n Побед: {user[3]}\n Поражений: {user[4]}", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "daily")
async def process_daily(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await safe_send(callback.message, "❌ Вы еще не зарегистрированы! Напишите /start")
        await callback.answer()
        return
    if user[6]:
        last_daily = datetime.fromisoformat(user[6])
        if datetime.now() - last_daily < timedelta(days=1):
            next_bonus = last_daily + timedelta(days=1)
            time_left = next_bonus - datetime.now()
            hours = int(time_left.total_seconds() // 3600)
            minutes = int((time_left.total_seconds() % 3600) // 60)
            await safe_send(callback.message, f"⏰ Вы уже получили ежедневный бонус!\n\nСледующий бонус через:\n🕐 {hours} ч. {minutes} мин.", reply_markup=back_to_menu_keyboard())
            await callback.answer()
            return
    await update_balance(callback.from_user.id, DAILY_BONUS)
    await update_daily(callback.from_user.id)
    await safe_send(callback.message, f"🎁 Вы получили ежедневный бонус!\n\n+{DAILY_BONUS} 💰 монет\nНовый баланс: {user[2] + DAILY_BONUS} ", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("game_"))
async def process_game_selection(callback: types.CallbackQuery):
    game_type = callback.data.replace("game_", "")
    player_bets[callback.from_user.id] = {"game": game_type}
    await safe_send(callback.message, "💸 Выберите ставку:", reply_markup=bet_keyboard(game_type))
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("bet_") and not c.data.endswith("_custom"))
async def process_bet(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    game_type, bet_amount = parts[1], int(parts[2])
    user = await get_user(callback.from_user.id)
    if user[2] < bet_amount:
        await safe_send(callback.message, f"❌ Недостаточно монет!\n\nВаш баланс: {user[2]} 💰\nНеобходимо: {bet_amount} 💰", reply_markup=back_to_menu_keyboard())
        await callback.answer()
        return
    await update_balance(callback.from_user.id, -bet_amount)
    player_bets[callback.from_user.id]["bet"] = bet_amount
    await start_game(callback.from_user.id, game_type, bet_amount, callback.message)
    await callback.answer()

@dp.callback_query(lambda c: c.data.endswith("_custom"))
async def process_custom_bet_request(callback: types.CallbackQuery):
    parts = callback.data.split("_")
    game_type = parts[1]
    user_id = callback.from_user.id
    if user_id not in player_bets: player_bets[user_id] = {"game": game_type}
    else: player_bets[user_id]["game"] = game_type
    waiting_for_bet[user_id] = {"game": game_type, "time": datetime.now()}
    user = await get_user(user_id)
    await safe_send(callback.message, f"💸 <b>Введите сумму ставки</b>\n\nВаш баланс: {user[2]} 💰\n\nПросто напишите число, например:\n5000\n\nУ вас есть {BET_TIMEOUT} секунд. Для отмены напишите /cancel", parse_mode="HTML")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "crash_cashout")
async def crash_cashout(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets or player_bets[user_id].get("game") != "crash":
        await callback.answer("Игра не найдена!", show_alert=True)
        return
    game = player_bets[user_id]
    if game.get("crash_cashed_out"):
        await callback.answer("Ты уже забрал выигрыш!", show_alert=True)
        return
    if game.get("crash_crashed"):
        await callback.answer("Ракета уже взорвалась!", show_alert=True)
        return
    game["crash_cashed_out"] = True
    multiplier = game["crash_multiplier"]
    bet = game["bet"]
    win_amount = int(bet * multiplier)
    await update_balance(user_id, win_amount)
    await update_stats(user_id, True)
    user = await get_user(user_id)
    await safe_send(callback.message, f"✅ <b>ЗАБРАЛ!</b>\n\nМножитель: <b>x{multiplier}</b>\n\n💰 Выигрыш: +{win_amount} монет\n Новый баланс: {user[2]} монет", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("mines_") and c.data in ["mines_3", "mines_5", "mines_10"])
async def mines_select_count(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets or player_bets[user_id].get("game") != "mines":
        await callback.answer("Сначала выберите игру!", show_alert=True)
        return
    mines_count = int(callback.data.split("_")[1])
    bet_amount = player_bets[user_id]["bet"]
    all_cells = list(range(25))
    mines = set(random.sample(all_cells, mines_count))
    player_bets[user_id] = {"game": "mines", "bet": bet_amount, "mines": mines, "revealed_cells": set(), "mines_count": mines_count, "multiplier": 1.0}
    await callback.message.edit_text(f"💣 <b>МИННОЕ ПОЛЕ</b>\n\n💰 Ставка: {bet_amount} монет\n💣 Мин: {mines_count}\n🎯 Множитель: x1.00\n\nОткрывай клетки и избегай мин!\nЧем больше откроешь — тем выше выигрыш!\n\nНажми на клетку, чтобы открыть:", parse_mode="HTML", reply_markup=mines_field_keyboard(set(), mines_count))
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("mines_cell_") and not c.data.endswith("_revealed"))
async def mines_reveal_cell(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets or player_bets[user_id].get("game") != "mines":
        await callback.answer("Игра не найдена!", show_alert=True)
        return
    game = player_bets[user_id]
    cell_id = int(callback.data.split("_")[2])
    if cell_id in game["revealed_cells"]:
        await callback.answer("Эта клетка уже открыта!", show_alert=True)
        return
    game["revealed_cells"].add(cell_id)
    if cell_id in game["mines"]:
        await update_stats(user_id, False)
        user = await get_user(user_id)
        await callback.message.edit_text(f"💥 <b>ВЗРЫВ!</b>\n\nТы попал на мину!\n\n💸 Проигрыш: -{game['bet']} монет\n💰 Баланс: {user[2]} монет", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
        if user_id in player_bets: del player_bets[user_id]
    else:
        revealed_count = len(game["revealed_cells"])
        multiplier = calculate_mines_multiplier(revealed_count, 25, game["mines_count"])
        game["multiplier"] = multiplier
        win_amount = int(game["bet"] * multiplier)
        await callback.message.edit_text(f"💣 <b>МИННОЕ ПОЛЕ</b>\n\n💰 Ставка: {game['bet']} монет\n💣 Мин: {game['mines_count']}\n✅ Открыто клеток: {revealed_count}\n🎯 Множитель: x{multiplier}\n💰 Текущий выигрыш: {win_amount} монет\n\nПродолжай открывать или забери выигрыш!", parse_mode="HTML", reply_markup=mines_field_keyboard(game["revealed_cells"], game["mines_count"]))
    await callback.answer()

@dp.callback_query(lambda c: c.data == "mines_cashout")
async def mines_cashout(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets or player_bets[user_id].get("game") != "mines":
        await callback.answer("Игра не найдена!", show_alert=True)
        return
    game = player_bets[user_id]
    revealed_count = len(game["revealed_cells"])
    if revealed_count == 0:
        await callback.answer("Открой хотя бы одну клетку!", show_alert=True)
        return
    multiplier = game["multiplier"]
    win_amount = int(game["bet"] * multiplier)
    await update_balance(user_id, win_amount)
    await update_stats(user_id, True)
    user = await get_user(user_id)
    await callback.message.edit_text(f"✅ <b>ВЫИГРЫШ ЗАБРАН!</b>\n\n🎯 Множитель: x{multiplier}\n✅ Открыто клеток: {revealed_count}\n\n💰 Выигрыш: +{win_amount} монет\n💰 Новый баланс: {user[2]} монет", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    if user_id in player_bets: del player_bets[user_id]
    await callback.answer()

@dp.callback_query(lambda c: c.data == "mines_surrender")
async def mines_surrender(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets or player_bets[user_id].get("game") != "mines":
        await callback.answer("Игра не найдена!", show_alert=True)
        return
    game = player_bets[user_id]
    revealed_count = len(game["revealed_cells"])
    if revealed_count == 0:
        await update_balance(user_id, game["bet"])
        await callback.message.edit_text(f"❌ Ты сдался до начала игры.\n\n💰 Ставка {game['bet']} монет возвращена.", reply_markup=back_to_menu_keyboard())
    else:
        await update_stats(user_id, False)
        user = await get_user(user_id)
        await callback.message.edit_text(f"❌ Ты сдался!\n\n💸 Проигрыш: -{game['bet']} монет\n💰 Баланс: {user[2]} монет", reply_markup=back_to_menu_keyboard())
    if user_id in player_bets: del player_bets[user_id]
    await callback.answer()

@dp.callback_query(lambda c: c.data == "bj_hit")
async def process_bj_hit(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets or player_bets[user_id].get("game") != "blackjack":
        await callback.answer("Игра не найдена!", show_alert=True)
        return
    game = player_bets[user_id]
    player_hand = game["player_hand"]
    player_hand.append(game["deck"].pop())
    player_score = calculate_score(player_hand)
    if player_score > 21:
        await update_stats(user_id, False)
        user = await get_user(user_id)
        await safe_send(callback.message, f"😔 <b>Перебор!</b>\n\nВаши карты: {format_hand(player_hand)} ({player_score})\n\n💸 Вы проиграли {game['bet']} монет.\n💰 Баланс: {user[2]}", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
        if user_id in player_bets: del player_bets[user_id]
    else:
        await safe_send(callback.message, f" <b>Блэкджек</b>\n💰 Ставка: {game['bet']} монет\n\nВаши карты: {format_hand(player_hand)} ({player_score})\nКарты дилера: {game['dealer_hand'][0]} и [скрыта]\n\nВыберите действие:", parse_mode="HTML", reply_markup=blackjack_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "bj_stand")
async def process_bj_stand(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets or player_bets[user_id].get("game") != "blackjack":
        await callback.answer("Игра не найдена!", show_alert=True)
        return
    game = player_bets[user_id]
    player_hand, dealer_hand, bet = game["player_hand"], game["dealer_hand"], game["bet"]
    player_score = calculate_score(player_hand)
    while calculate_score(dealer_hand) < 17: dealer_hand.append(game["deck"].pop())
    dealer_score = calculate_score(dealer_hand)
    user = await get_user(user_id)
    if dealer_score > 21 or player_score > dealer_score:
        win_amount = bet * 2
        await update_balance(user_id, win_amount)
        await update_stats(user_id, True)
        user = await get_user(user_id)
        text = f"🎉 <b>Вы выиграли!</b>\n\nВаши карты: {format_hand(player_hand)} ({player_score})\nКарты дилера: {format_hand(dealer_hand)} ({dealer_score})\n\n💰 Выигрыш: +{win_amount} монет\n💰 Баланс: {user[2]}"
    elif player_score < dealer_score:
        await update_stats(user_id, False)
        text = f"😔 <b>Вы проиграли!</b>\n\nВаши карты: {format_hand(player_hand)} ({player_score})\nКарты дилера: {format_hand(dealer_hand)} ({dealer_score})\n\n💸 Проигрыш: -{bet} монет\n💰 Баланс: {user[2]}"
    else:
        await update_balance(user_id, bet)
        text = f"🤝 <b>Ничья!</b>\n\nВаши карты: {format_hand(player_hand)} ({player_score})\nКарты дилера: {format_hand(dealer_hand)} ({dealer_score})\n\n💰 Ставка возвращена.\n💰 Баланс: {user[2]}"
    await safe_send(callback.message, text, parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    if user_id in player_bets: del player_bets[user_id]
    await callback.answer()

@dp.callback_query(lambda c: c.data == "slots_spin")
async def process_slots_spin(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets:
        await callback.answer("Сначала выберите игру!", show_alert=True)
        return
    bet = player_bets[user_id]["bet"]
    reel1, reel2, reel3 = random.choice(SLOT_SYMBOLS), random.choice(SLOT_SYMBOLS), random.choice(SLOT_SYMBOLS)
    won, win_amount, win_text = False, 0, ""
    if reel1 == reel2 == reel3: won, win_amount, win_text = True, bet * 10, "🎉 ДЖЕКПОТ! Все три символа совпали!"
    elif reel1 == reel2 or reel2 == reel3 or reel1 == reel3: won, win_amount, win_text = True, bet * 2, "✨ Неплохо! Два символа совпали!"
    else: win_text = "😔 Не повезло. Все символы разные."
    if won:
        await update_balance(user_id, win_amount)
        await update_stats(user_id, True)
    else: await update_stats(user_id, False)
    user = await get_user(user_id)
    result_display = f"🎰 {reel1} | {reel2} | {reel3}"
    if won: message_text = f"{win_text}\n\n{result_display}\n\n💰 Выигрыш: +{win_amount} монет\n Новый баланс: {user[2]} монет"
    else: message_text = f"{win_text}\n\n{result_display}\n\n💸 Проигрыш: -{bet} монет\n💰 Остаток: {user[2]} монет"
    await safe_send(callback.message, message_text, reply_markup=back_to_menu_keyboard())
    if user_id in player_bets: del player_bets[user_id]
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("coin_"))
async def process_coin_game(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets:
        await callback.answer("Сначала выберите игру!", show_alert=True)
        return
    bet = player_bets[user_id]["bet"]
    choice = callback.data.replace("coin_", "")
    result = random.choice(["heads", "tails"])
    won = (choice == result)
    win_amount = bet * 2 if won else 0
    if won:
        await update_balance(user_id, win_amount)
        await update_stats(user_id, True)
    else: await update_stats(user_id, False)
    user = await get_user(user_id)
    result_text = "🦊 Орёл" if result == "heads" else "🌐 Решка"
    choice_text = "🦊 Орёл" if choice == "heads" else "🌐 Решка"
    if won: text = f"🎉 Поздравляем! Вы выиграли!\n\nВаш выбор: {choice_text}\nВыпало: {result_text}\n\n💰 Выигрыш: +{win_amount} монет\n💰 Новый баланс: {user[2]} монет"
    else: text = f"😔 К сожалению, вы проиграли\n\nВаш выбор: {choice_text}\nВыпало: {result_text}\n\n💸 Проигрыш: -{bet} монет\n Остаток: {user[2]} монет"
    await safe_send(callback.message, text, reply_markup=back_to_menu_keyboard())
    if user_id in player_bets: del player_bets[user_id]
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("dice_"))
async def process_dice_game(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets:
        await callback.answer("Сначала выберите игру!", show_alert=True)
        return
    bet = player_bets[user_id]["bet"]
    choice = int(callback.data.replace("dice_", ""))
    result = random.randint(1, 6)
    won = (choice == result)
    win_amount = bet * 6 if won else 0
    if won:
        await update_balance(user_id, win_amount)
        await update_stats(user_id, True)
    else: await update_stats(user_id, False)
    user = await get_user(user_id)
    if won: text = f"🎉 Поздравляем! Вы выиграли!\n\nВаш выбор: {choice}️⃣\nВыпало: {result}️⃣\n\n Выигрыш: +{win_amount} монет (x6)\n💰 Новый баланс: {user[2]} монет"
    else: text = f"😔 К сожалению, вы проиграли\n\nВаш выбор: {choice}️⃣\nВыпало: {result}️⃣\n\n💸 Проигрыш: -{bet} монет\n💰 Остаток: {user[2]} монет"
    await safe_send(callback.message, text, reply_markup=back_to_menu_keyboard())
    if user_id in player_bets: del player_bets[user_id]
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("roulette_"))
async def process_roulette_game(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in player_bets:
        await callback.answer("Сначала выберите игру!", show_alert=True)
        return
    bet = player_bets[user_id]["bet"]
    choice = callback.data.replace("roulette_", "")
    result = random.randint(0, 36)
    red_numbers = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
    is_red = result in red_numbers
    won, win_amount = False, 0
    if choice == "red" and is_red and result != 0: won, win_amount = True, bet * 2
    elif choice == "black" and not is_red and result != 0: won, win_amount = True, bet * 2
    if won:
        await update_balance(user_id, win_amount)
        await update_stats(user_id, True)
    else: await update_stats(user_id, False)
    user = await get_user(user_id)
    color = "🔴 Красное" if is_red else ("⚫ Черное" if result != 0 else "🟢 Зеро")
    choice_text = "🔴 Красное" if choice == "red" else "⚫ Черное"
    if won: text = f"🎉 Поздравляем! Вы выиграли!\n\nВаша ставка: {choice_text}\nВыпало: {result} ({color})\n\n💰 Выигрыш: +{win_amount} монет (x2)\n💰 Новый баланс: {user[2]} монет"
    else: text = f"😔 К сожалению, вы проиграли\n\nВаша ставка: {choice_text}\nВыпало: {result} ({color})\n\n💸 Проигрыш: -{bet} монет\n Остаток: {user[2]} монет"
    await safe_send(callback.message, text, reply_markup=back_to_menu_keyboard())
    if user_id in player_bets: del player_bets[user_id]
    await callback.answer()

@dp.callback_query(lambda c: c.data == "top")
async def process_top(callback: types.CallbackQuery):
    top_users = await get_top_users(10)
    if not top_users:
        await safe_send(callback.message, "🏆 Топ игроков пока пуст!", reply_markup=back_to_menu_keyboard())
        await callback.answer()
        return
    text = "🏆 Топ-10 игроков:\n\n" + "".join([f"{i}. @{(user[0] if user[0] else 'Игрок')} - {user[1]} 💰\n" for i, user in enumerate(top_users, 1)])
    await safe_send(callback.message, text, reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "stats")
async def process_stats(callback: types.CallbackQuery):
    user = await get_user(callback.from_user.id)
    if not user:
        await safe_send(callback.message, "❌ Вы еще не зарегистрированы! Напишите /start")
        await callback.answer()
        return
    win_rate = (user[3] / user[5] * 100) if user[5] > 0 else 0
    await safe_send(callback.message, f"📊 Ваша статистика:\n\n💰 Баланс: {user[2]} монет\n🎮 Игр сыграно: {user[5]}\n✅ Побед: {user[3]}\n❌ Поражений: {user[4]}\n📈 Процент побед: {win_rate:.1f}%", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "help")
async def process_help_button(callback: types.CallbackQuery):
    text = "📜 <b>Список команд бота:</b>\n\n <b>Основные:</b>\n/start - Запустить бота\n/balance - Проверить баланс\n/daily - Ежедневный бонус\n/games - Меню игр\n/top - Топ игроков\n/stats - Моя статистика\n/promo КОД - Активировать промокод\n/gift @username СУММА - Подарить монеты\n\n👥 <b>Групповые игры:</b>\n/duel @username СУММА - Дуэль 1 на 1\n/survival СУММА - Выживание\n/survivalstart - Начать выживание\n/jackpot СУММА СЕКУНДЫ - Джекпот\n/race СУММА - Гонки\n/racestart - Начать гонку\n/battle СУММА - Командная битва\n/battlestart - Начать битву\n/wheel СУММА - Колесо Фортуны\n/wheelstart - Начать вращение\n/join - Вступить в игру\n\n🛠 <b>Администратор:</b>\n/admin - Админ-панель\n/users - Статистика пользователей\n/broadcast - Сделать рассылку\n\n️ <b>Важно:</b> В одной группе одновременно может идти не более 2 игр. Если в чате уже идут игры — дождитесь их окончания!"
    await safe_send(callback.message, text, parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "promo")
async def process_promo_button(callback: types.CallbackQuery):
    await safe_send(callback.message, "️ <b>Активация промокода</b>\n\nВведите команду в чат:\n/promo КОД\n\nПример: /promo SORRY", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "gift")
async def process_gift_button(callback: types.CallbackQuery):
    await safe_send(callback.message, "🎁 <b>Перевод монет</b>\n\nЧтобы подарить монеты, введите в чат:\n/gift @username СУММА\n\nПример: /gift @durov 500\n\n💡 У получателя должен быть установлен username в Telegram!", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_broadcast")
async def process_admin_broadcast(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет прав!", show_alert=True)
        return
    broadcast_users.add(callback.from_user.id)
    await safe_send(callback.message, "📡 <b>Режим рассылки активирован!</b>\n\nПришлите текст, фото или видео для рассылки.\nДля отмены напишите /cancel", parse_mode="HTML", reply_markup=back_to_menu_keyboard())
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin_users")
async def process_admin_users(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ У вас нет прав!", show_alert=True)
        return
    stats = await get_users_count()
    win_rate = (stats['total_wins'] / stats['total_games'] * 100) if stats['total_games'] > 0 else 0
    text = f" <b>Статистика пользователей</b>\n\n👥 <b>Всего пользователей:</b> {stats['total']}\n✅ Активных: {stats['active']}\n🚫 Заблокированных: {stats['banned']}\n\n💰 <b>Всего монет в экономике:</b> {stats['total_balance']}\n\n🎮 <b>Всего игр сыграно:</b> {stats['total_games']}\n✅ Всего побед: {stats['total_wins']}\n📈 Процент побед: {win_rate:.1f}%"
    await safe_send(callback.message, text, parse_mode="HTML", reply_markup=admin_menu_keyboard())
    await callback.answer()

@dp.message(Command("createpromo"))
async def cmd_create_promo(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await safe_send(message, "⛔ У вас нет прав администратора!")
        return
    args = message.text.split()
    if len(args) != 3:
        await safe_send(message, "Использование: /createpromo КОД СУММА\nПример: /createpromo TEST 5000")
        return
    code = args[1].upper()
    try: amount = int(args[2])
    except ValueError:
        await safe_send(message, "Сумма должна быть числом!")
        return
    await create_promo(code, amount, 9999, "2030-01-01", message.from_user.id)
    await safe_send(message, f"✅ Промокод {code} на {amount} монет успешно создан!")

@dp.message(Command("promo"))
async def cmd_activate_promo(message: types.Message):
    args = message.text.split()
    if len(args) != 2:
        await safe_send(message, "Использование: /promo КОД\nПример: /promo SORRY")
        return
    code = args[1].upper()
    promo = await get_promo(code)
    if not promo:
        await safe_send(message, "❌ Промокод не найден!")
        return
    activation = await check_promo_activation(code, message.from_user.id)
    if activation:
        await safe_send(message, "❌ Вы уже активировали этот промокод!")
        return
    amount = promo[1]
    await update_balance(message.from_user.id, amount)
    await activate_promo(code, message.from_user.id)
    user = await get_user(message.from_user.id)
    await safe_send(message, f"🎁 Промокод активирован! Начислено +{amount} монет.\n💰 Ваш баланс: {user[2]}")

async def main():
    await init_db()
    logging.info("База данных инициализирована")
    logging.info("Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен")