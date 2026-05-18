import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

import gspread
from flask import Flask, request
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

SHEET_FINES = os.environ.get("SHEET_FINES", "Штрафи")
SHEET_WARNINGS = os.environ.get("SHEET_WARNINGS", "Попередження")
SHEET_EMPLOYEES = os.environ.get("SHEET_EMPLOYEES", "Працівники")
SHEET_LOGS = os.environ.get("SHEET_LOGS", "Логи")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "telegram-webhook")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

app = Flask(__name__)
telegram_app: Optional[Application] = None


def get_client():
    creds = json.loads(GOOGLE_CREDENTIALS_JSON)
    return gspread.service_account_from_dict(creds)


def get_spreadsheet():
    return get_client().open_by_key(SPREADSHEET_ID)


def ws(sheet_name: str):
    return get_spreadsheet().worksheet(sheet_name)


def normalize(value: Any) -> str:
    return str(value or "").strip()


def find_col(headers: List[str], variants: List[str]) -> Optional[int]:
    clean = [h.strip().lower() for h in headers]
    for v in variants:
        v = v.strip().lower()
        if v in clean:
            return clean.index(v)
    for i, h in enumerate(clean):
        for v in variants:
            if v.strip().lower() in h:
                return i
    return None


def get_employee_name_by_tg(telegram_id: int) -> Optional[str]:
    try:
        values = ws(SHEET_EMPLOYEES).get_all_values()
        if not values:
            return None

        headers = values[0]
        name_col = find_col(headers, ["Співробітник", "ПІБ", "Працівник", "Ім'я", "ПІБ співробітника"])
        tg_col = find_col(headers, ["Telegram ID", "Телеграм ID", "telegram_id", "tg id", "TG ID"])

        if name_col is None or tg_col is None:
            return None

        for row in values[1:]:
            if len(row) > tg_col and normalize(row[tg_col]) == str(telegram_id):
                return normalize(row[name_col]) if len(row) > name_col else None
    except Exception as e:
        logger.exception(e)
    return None


def get_records(sheet_name: str, telegram_id: int, only_new: bool = True, category: Optional[str] = None) -> List[Dict[str, Any]]:
    employee_name = get_employee_name_by_tg(telegram_id)

    values = ws(sheet_name).get_all_values()
    if not values:
        return []

    headers = values[0]

    employee_col = find_col(headers, ["Співробітник", "ПІБ", "Працівник", "Менеджер"])
    tg_col = find_col(headers, ["Telegram ID", "Телеграм ID", "telegram_id", "tg id", "TG ID"])
    viewed_col = find_col(headers, ["Переглянуто", "Переглянутий", "Viewed", "Статус"])
    category_col = find_col(headers, ["Категорія", "Категория", "Тип"])

    result = []

    for row_index, row in enumerate(values[1:], start=2):
        def cell(col):
            return normalize(row[col]) if col is not None and len(row) > col else ""

        allowed = False

        if tg_col is not None and cell(tg_col) == str(telegram_id):
            allowed = True

        if not allowed and employee_name and employee_col is not None and cell(employee_col) == employee_name:
            allowed = True

        if not allowed:
            continue

        if only_new and viewed_col is not None and cell(viewed_col).lower() in ["так", "yes", "true", "переглянуто"]:
            continue

        if category and category_col is not None and cell(category_col) != category:
            continue

        result.append({
            "row_index": row_index,
            "headers": headers,
            "row": row,
        })

    return result


def get_categories(sheet_name: str, telegram_id: int) -> List[str]:
    records = get_records(sheet_name, telegram_id, only_new=False)
    categories = set()

    for rec in records:
        headers = rec["headers"]
        row = rec["row"]
        category_col = find_col(headers, ["Категорія", "Категория", "Тип"])
        if category_col is not None and len(row) > category_col:
            category = normalize(row[category_col])
            if category:
                categories.add(category)

    return sorted(categories)


def get_value(rec: Dict[str, Any], variants: List[str]) -> str:
    headers = rec["headers"]
    row = rec["row"]
    col = find_col(headers, variants)
    if col is None or len(row) <= col:
        return "-"
    return normalize(row[col]) or "-"


def build_record_text(title: str, rec: Dict[str, Any], index: int, total: int, sheet_name: str) -> str:
    lines = [
        title,
        "",
        f"📄 Запис {index + 1} з {total}",
        "",
        f"🆔 ID: {get_value(rec, ['ID', 'ID порушення', '№'])}",
        f"📅 Дата надходження: {get_value(rec, ['Дата надходження', 'Дата'])}",
        f"📅 Дата фіксації: {get_value(rec, ['Дата фіксації', 'Дата порушення'])}",
        f"👤 Співробітник: {get_value(rec, ['Співробітник', 'ПІБ', 'Працівник', 'Менеджер'])}",
        f"📂 Категорія: {get_value(rec, ['Категорія', 'Категория', 'Тип'])}",
    ]

    if sheet_name == SHEET_FINES:
        lines.append(f"💰 Сума: {get_value(rec, ['Сума', 'Штраф', 'Сума штрафу'])} грн")

    lines += [
        f"🧾 № ліда: {get_value(rec, ['№ ліда', 'Номер ліда', 'Лід', 'ID ліда'])}",
        "",
        f"📝 Суть:\n{get_value(rec, ['Суть', 'Опис', 'Коментар', 'Причина'])}",
    ]

    return "\n".join(lines)


def menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🆕 Нові штрафи", callback_data="new|fines"),
            InlineKeyboardButton("⚠️ Нові попередження", callback_data="new|warnings"),
        ],
        [InlineKeyboardButton("📂 Штрафи по категоріях", callback_data="cats|fines")],
        [InlineKeyboardButton("📁 Попередження по категоріях", callback_data="cats|warnings")],
    ])


def back_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu")]])


def sheet_by_code(code: str) -> str:
    return SHEET_FINES if code == "fines" else SHEET_WARNINGS


def title_by_code(code: str, category: Optional[str] = None) -> str:
    if category:
        return f"📂 Штрафи: {category}" if code == "fines" else f"📁 Попередження: {category}"
    return "🆕 Нові штрафи" if code == "fines" else "⚠️ Нові попередження"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📋 Меню\n\nОберіть розділ:", reply_markup=menu_keyboard())


async def show_records(query, code: str, index: int = 0, category: Optional[str] = None, only_new: bool = True):
    sheet_name = sheet_by_code(code)
    records = get_records(sheet_name, query.from_user.id, only_new=only_new, category=category)
    title = title_by_code(code, category)

    if not records:
        text = title + "\n\n✅ Записів немає."
        await query.edit_message_text(text, reply_markup=back_menu_keyboard())
        return

    index = max(0, min(index, len(records) - 1))
    rec = records[index]
    text = build_record_text(title, rec, index, len(records), sheet_name)

    buttons = []
    nav = []

    page_action = "catpage" if category else "page"
    if index > 0:
        nav.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"{page_action}|{code}|{index-1}|{category or ''}"))
    if index < len(records) - 1:
        nav.append(InlineKeyboardButton("➡️ Далі", callback_data=f"{page_action}|{code}|{index+1}|{category or ''}"))
    if nav:
        buttons.append(nav)

    if only_new and not category:
        buttons.append([InlineKeyboardButton("✅ Переглянуто", callback_data=f"viewed|{code}|{rec['row_index']}")])

    if category:
        buttons.append([InlineKeyboardButton("⬅️ До категорій", callback_data=f"cats|{code}")])

    buttons.append([InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


def mark_viewed(sheet_name: str, row_index: int):
    worksheet = ws(sheet_name)
    headers = worksheet.row_values(1)
    viewed_col = find_col(headers, ["Переглянуто", "Переглянутий", "Viewed", "Статус"])

    if viewed_col is None:
        return

    worksheet.update_cell(row_index, viewed_col + 1, "Так")


async def show_categories(query, code: str):
    sheet_name = sheet_by_code(code)
    categories = get_categories(sheet_name, query.from_user.id)
    title = "📂 Штрафи по категоріях" if code == "fines" else "📁 Попередження по категоріях"

    if not categories:
        await query.edit_message_text(title + "\n\nКатегорій поки немає.", reply_markup=back_menu_keyboard())
        return

    buttons = [[InlineKeyboardButton("📌 " + cat, callback_data=f"catopen|{code}|{cat}")] for cat in categories]
    buttons.append([InlineKeyboardButton("⬅️ Назад в меню", callback_data="menu")])

    await query.edit_message_text(title + "\n\nОберіть категорію:", reply_markup=InlineKeyboardMarkup(buttons))


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    parts = data.split("|")
    action = parts[0]

    if action == "menu":
        await query.edit_message_text("📋 Меню\n\nОберіть розділ:", reply_markup=menu_keyboard())
        return

    if action == "new":
        await show_records(query, parts[1], 0, only_new=True)
        return

    if action == "page":
        await show_records(query, parts[1], int(parts[2]), only_new=True)
        return

    if action == "viewed":
        code = parts[1]
        row_index = int(parts[2])
        mark_viewed(sheet_by_code(code), row_index)
        await show_records(query, code, 0, only_new=True)
        return

    if action == "cats":
        await show_categories(query, parts[1])
        return

    if action == "catopen":
        await show_records(query, parts[1], 0, category=parts[2], only_new=False)
        return

    if action == "catpage":
        await show_records(query, parts[1], int(parts[2]), category=parts[3], only_new=False)
        return


@app.route("/")
def health():
    return "Bot is running"


@app.route(f"/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), telegram_app.bot)
    telegram_app.update_queue.put_nowait(update)
    return "ok"


def create_app():
    global telegram_app

    telegram_app = Application.builder().token(BOT_TOKEN).build()
    telegram_app.add_handler(CommandHandler(["start", "menu"], start))
    telegram_app.add_handler(CallbackQueryHandler(callback_handler))

    return app


flask_app = create_app()


@app.before_request
def ensure_started():
    if not telegram_app.running:
        telegram_app.initialize()
        telegram_app.start()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)