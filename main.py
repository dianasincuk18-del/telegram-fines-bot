import os
import json
import logging
from typing import Any, Dict, List, Optional

import gspread
from flask import Flask, request
import requests

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

app = Flask(__name__)


def tg(method: str, payload: Dict[str, Any]):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    r = requests.post(url, json=payload, timeout=20)
    try:
        data = r.json()
    except Exception:
        data = {"ok": False, "text": r.text}
    logger.info("Telegram %s response: %s", method, data)
    return data


def send_message(chat_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None):
    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("sendMessage", payload)


def edit_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("editMessageText", payload)


def answer_callback(callback_query_id: str):
    return tg("answerCallbackQuery", {"callback_query_id": callback_query_id})


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


def inline_keyboard(buttons: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"inline_keyboard": buttons}


def button(text: str, callback_data: str) -> Dict[str, str]:
    return {"text": text, "callback_data": callback_data}


def menu_keyboard():
    return inline_keyboard([
        [
            button("🆕 Нові штрафи", "new|fines"),
            button("⚠️ Нові попередження", "new|warnings"),
        ],
        [button("📂 Штрафи по категоріях", "cats|fines")],
        [button("📁 Попередження по категоріях", "cats|warnings")],
    ])


def back_menu_keyboard():
    return inline_keyboard([[button("⬅️ Назад в меню", "menu")]])


def sheet_by_code(code: str) -> str:
    return SHEET_FINES if code == "fines" else SHEET_WARNINGS


def title_by_code(code: str, category: Optional[str] = None) -> str:
    if category:
        return f"📂 Штрафи: {category}" if code == "fines" else f"📁 Попередження: {category}"
    return "🆕 Нові штрафи" if code == "fines" else "⚠️ Нові попередження"


def show_records(chat_id: int, message_id: int, telegram_id: int, code: str, index: int = 0, category: Optional[str] = None, only_new: bool = True):
    sheet_name = sheet_by_code(code)
    records = get_records(sheet_name, telegram_id, only_new=only_new, category=category)
    title = title_by_code(code, category)

    if not records:
        edit_message(chat_id, message_id, title + "\n\n✅ Записів немає.", back_menu_keyboard())
        return

    index = max(0, min(index, len(records) - 1))
    rec = records[index]
    text = build_record_text(title, rec, index, len(records), sheet_name)

    buttons = []
    nav = []

    page_action = "catpage" if category else "page"

    if index > 0:
        nav.append(button("⬅️ Назад", f"{page_action}|{code}|{index - 1}|{category or ''}"))
    if index < len(records) - 1:
        nav.append(button("➡️ Далі", f"{page_action}|{code}|{index + 1}|{category or ''}"))

    if nav:
        buttons.append(nav)

    if only_new and not category:
        buttons.append([button("✅ Переглянуто", f"viewed|{code}|{rec['row_index']}")])

    if category:
        buttons.append([button("⬅️ До категорій", f"cats|{code}")])

    buttons.append([button("⬅️ Назад в меню", "menu")])

    edit_message(chat_id, message_id, text, inline_keyboard(buttons))


def mark_viewed(sheet_name: str, row_index: int):
    worksheet = ws(sheet_name)
    headers = worksheet.row_values(1)
    viewed_col = find_col(headers, ["Переглянуто", "Переглянутий", "Viewed", "Статус"])

    if viewed_col is None:
        return

    worksheet.update_cell(row_index, viewed_col + 1, "Так")


def show_categories(chat_id: int, message_id: int, telegram_id: int, code: str):
    sheet_name = sheet_by_code(code)
    categories = get_categories(sheet_name, telegram_id)

    title = "📂 Штрафи по категоріях" if code == "fines" else "📁 Попередження по категоріях"

    if not categories:
        edit_message(chat_id, message_id, title + "\n\nКатегорій поки немає.", back_menu_keyboard())
        return

    buttons = [[button("📌 " + cat, f"catopen|{code}|{cat}")] for cat in categories]
    buttons.append([button("⬅️ Назад в меню", "menu")])

    edit_message(chat_id, message_id, title + "\n\nОберіть категорію:", inline_keyboard(buttons))


def handle_callback(callback_query: Dict[str, Any]):
    answer_callback(callback_query["id"])

    data = callback_query.get("data", "")
    parts = data.split("|")

    action = parts[0]
    message = callback_query["message"]
    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    telegram_id = callback_query["from"]["id"]

    if action == "menu":
        edit_message(chat_id, message_id, "📋 Меню\n\nОберіть розділ:", menu_keyboard())
        return

    if action == "new":
        show_records(chat_id, message_id, telegram_id, parts[1], 0, only_new=True)
        return

    if action == "page":
        show_records(chat_id, message_id, telegram_id, parts[1], int(parts[2]), only_new=True)
        return

    if action == "viewed":
        code = parts[1]
        row_index = int(parts[2])
        mark_viewed(sheet_by_code(code), row_index)
        show_records(chat_id, message_id, telegram_id, code, 0, only_new=True)
        return

    if action == "cats":
        show_categories(chat_id, message_id, telegram_id, parts[1])
        return

    if action == "catopen":
        show_records(chat_id, message_id, telegram_id, parts[1], 0, category=parts[2], only_new=False)
        return

    if action == "catpage":
        show_records(chat_id, message_id, telegram_id, parts[1], int(parts[2]), category=parts[3], only_new=False)
        return


def handle_message(message: Dict[str, Any]):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text in ["/start", "/menu", "старт", "Старт"]:
        send_message(chat_id, "📋 Меню\n\nОберіть розділ:", menu_keyboard())
    else:
        send_message(chat_id, "Напишіть /start, щоб відкрити меню.")


@app.route("/")
def health():
    return "Bot is running"


@app.route(f"/{WEBHOOK_SECRET}", methods=["POST"])
def webhook():
    update = request.get_json(force=True)
    logger.info("Incoming update: %s", update)

    try:
        if "message" in update:
            handle_message(update["message"])

        if "callback_query" in update:
            handle_callback(update["callback_query"])

    except Exception as e:
        logger.exception(e)

        try:
            if "message" in update:
                chat_id = update["message"]["chat"]["id"]
            else:
                chat_id = update["callback_query"]["message"]["chat"]["id"]

            send_message(chat_id, "❌ Сталася помилка. Напишіть адміністратору або спробуйте ще раз.")
        except Exception:
            pass

    return "ok"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
