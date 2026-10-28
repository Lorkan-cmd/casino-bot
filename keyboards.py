from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=" Одиночные игры", callback_data="solo_games"),
            InlineKeyboardButton(text="👥 Групповые игры", callback_data="group_games")
        ],
        [
            InlineKeyboardButton(text=" Баланс", callback_data="balance"),
            InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data="daily")
        ],
        [
            InlineKeyboardButton(text="🏆 Топ игроков", callback_data="top"),
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats")
        ],
        [
            InlineKeyboardButton(text="🎟️ Промокод", callback_data="promo"),
            InlineKeyboardButton(text="🎁 Подарить", callback_data="gift")
        ],
        [
            InlineKeyboardButton(text="❓ Помощь", callback_data="help")
        ]
    ])
    return keyboard

def solo_games_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎰 Слоты", callback_data="game_slots"),
            InlineKeyboardButton(text="🎲 Кости", callback_data="game_dice")
        ],
        [
            InlineKeyboardButton(text="🪙 Монетка", callback_data="game_coin"),
            InlineKeyboardButton(text="🎯 Рулетка", callback_data="game_roulette")
        ],
        [
            InlineKeyboardButton(text=" Блэкджек", callback_data="game_blackjack"),
            InlineKeyboardButton(text=" Крэш", callback_data="game_crash")
        ],
        [
            InlineKeyboardButton(text="💣 Минное поле", callback_data="game_mines")
        ],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")
        ]
    ])
    return keyboard

def group_games_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⚔️ Дуэль (1 на 1)", callback_data="group_duel"),
            InlineKeyboardButton(text="💀 Выживание", callback_data="group_survival")
        ],
        [
            InlineKeyboardButton(text="🎰 Джекпот", callback_data="group_jackpot"),
            InlineKeyboardButton(text="🏇 Гонки", callback_data="group_race")
        ],
        [
            InlineKeyboardButton(text="⚔️ Командная битва", callback_data="group_battle"),
            InlineKeyboardButton(text="🎡 Колесо Фортуны", callback_data="group_wheel")
        ],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")
        ]
    ])
    return keyboard

def bet_keyboard(game_type: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="10 💰", callback_data=f"bet_{game_type}_10"),
            InlineKeyboardButton(text="50 💰", callback_data=f"bet_{game_type}_50"),
            InlineKeyboardButton(text="100 💰", callback_data=f"bet_{game_type}_100")
        ],
        [
            InlineKeyboardButton(text="500 💰", callback_data=f"bet_{game_type}_500"),
            InlineKeyboardButton(text="1000 💰", callback_data=f"bet_{game_type}_1000"),
            InlineKeyboardButton(text="💸 Своя ставка", callback_data=f"bet_{game_type}_custom")
        ],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="solo_games")
        ]
    ])
    return keyboard

def mines_count_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="3 мины (легко)", callback_data="mines_3"),
            InlineKeyboardButton(text="5 мин (средне)", callback_data="mines_5")
        ],
        [
            InlineKeyboardButton(text="10 мин (сложно)", callback_data="mines_10")
        ],
        [
            InlineKeyboardButton(text="◀️ Назад", callback_data="solo_games")
        ]
    ])
    return keyboard

def mines_field_keyboard(revealed_cells, mines_count):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])
    for row in range(5):
        row_buttons = []
        for col in range(5):
            cell_id = row * 5 + col
            if cell_id in revealed_cells:
                row_buttons.append(InlineKeyboardButton(text="✅", callback_data=f"mines_cell_{cell_id}_revealed"))
            else:
                row_buttons.append(InlineKeyboardButton(text="", callback_data=f"mines_cell_{cell_id}"))
        keyboard.inline_keyboard.append(row_buttons)
    if len(revealed_cells) > 0:
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="💰 ЗАБРАТЬ!", callback_data="mines_cashout")
        ])
    keyboard.inline_keyboard.append([
        InlineKeyboardButton(text=" Сдаться", callback_data="mines_surrender")
    ])
    return keyboard

def coin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=" Орёл", callback_data="coin_heads"),
        InlineKeyboardButton(text="🌐 Решка", callback_data="coin_tails")
    ]])

def dice_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣", callback_data="dice_1"), InlineKeyboardButton(text="2️⃣", callback_data="dice_2"), InlineKeyboardButton(text="3️⃣", callback_data="dice_3")],
        [InlineKeyboardButton(text="4️", callback_data="dice_4"), InlineKeyboardButton(text="5️⃣", callback_data="dice_5"), InlineKeyboardButton(text="6️", callback_data="dice_6")]
    ])

def roulette_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Красное", callback_data="roulette_red"), InlineKeyboardButton(text="⚫ Черное", callback_data="roulette_black")],
        [InlineKeyboardButton(text="🔢 Число (0-36)", callback_data="roulette_number")]
    ])

def slots_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎰 КРУТИТЬ!", callback_data="slots_spin")]])

def blackjack_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=" Ещё", callback_data="bj_hit"),
        InlineKeyboardButton(text="✋ Хватит", callback_data="bj_stand")
    ]])

def crash_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💰 ЗАБРАТЬ!", callback_data="crash_cashout")]])

def duel_response_keyboard(challenger_id: int, opponent_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⚔️ Принять дуэль", callback_data=f"duel_accept_{challenger_id}_{opponent_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"duel_decline_{challenger_id}_{opponent_id}")
    ]])

def survival_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎲 Бросить кубик!", callback_data="survival_roll")]])

def admin_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Создать рассылку", callback_data="admin_broadcast"), InlineKeyboardButton(text="🎟️ Создать промокод", callback_data="admin_promo")],
        [InlineKeyboardButton(text=" Пользователи", callback_data="admin_users"), InlineKeyboardButton(text="📊 Статистика бота", callback_data="admin_stats")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")]
    ])

def back_to_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")]])