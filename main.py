import os
import json
import logging
from datetime import datetime
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
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "telegram-webhook")

app = Flask(__name__)

MONTHS_UA = {
    1: "Січень", 2: "Лютий", 3: "Березень", 4: "Квітень",
    5: "Травень", 6: "Червень", 7: "Липень", 8: "Серпень",
    9: "Вересень", 10: "Жовтень", 11: "Листопад", 12: "Грудень"
}


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
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("sendMessage", payload)


def edit_message(chat_id: int, message_id: int, text: str, reply_markup: Optional[Dict[str, Any]] = None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg("editMessageText", payload)


def answer_callback(callback_query_id: str):
    return tg("answerCallbackQuery", {"callback_query_id": callback_query_id})


def get_client():
    creds = json.loads(GOOGLE_CREDENTIALS_JSON)
    return gspread.service_account_from_dict(creds)


def ws(sheet_name: str):
    return get_client().open_by_key(SPREADSHEET_ID).worksheet(sheet_name)


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


def parse_date(value: str) -> Optional[datetime]:
    value = normalize(value)
    if not value:
        return None

    formats = [
        "%d.%m.%Y",
        "%d.%m.%y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d/%m/%y",
        "%m/%d/%Y",
        "%Y/%m/%d",
        "%d.%m.%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass

    return None


def month_key_from_date(value: str) -> Optional[str]:
    d = parse_date(value)
    if not d:
        return None
    return f"{d.year}-{d.month:02d}"


def month_label(month_key: str) -> str:
    year, month = month_key.split("-")
    return f"{MONTHS_UA[int(month)]} {year}"


def inline_keyboard(buttons: List[List[Dict[str, str]]]) -> Dict[str, Any]:
    return {"inline_keyboard": buttons}


def button(text: str, callback_data: str) -> Dict[str, str]:
    return {"text": text, "callback_data": callback_data}


def back_menu_keyboard():
    return inline_keyboard([[button("⬅️ Назад в меню", "menu")]])


def sheet_by_code(code: str) -> str:
    return SHEET_FINES if code == "f" else SHEET_WARNINGS


def section_title(code: str) -> str:
    return "📂 Штрафи по категоріях" if code == "f" else "📁 Попередження по категоріях"


def new_title(code: str) -> str:
    return "🆕 Нові штрафи" if code == "f" else "⚠️ Нові попередження"


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


def get_employee_profile_by_tg(telegram_id: int) -> Optional[Dict[str, str]]:
    try:
        values = ws(SHEET_EMPLOYEES).get_all_values()
        if not values:
            return None

        headers = values[0]
        name_col = find_col(headers, ["Співробітник", "ПІБ", "Працівник", "Ім'я", "ПІБ співробітника"])
        tg_col = find_col(headers, ["Telegram ID", "Телеграм ID", "telegram_id", "tg id", "TG ID"])
        active_col = find_col(headers, ["Активний", "Активна", "Active"])
        role_col = find_col(headers, ["Роль", "Role"])
        manager_col = find_col(headers, ["Керівник", "Куратор", "Manager"])

        if name_col is None or tg_col is None:
            return None

        for row in values[1:]:
            def cell(col):
                return normalize(row[col]) if col is not None and len(row) > col else ""

            if cell(tg_col) == str(telegram_id):
                return {
                    "name": cell(name_col),
                    "telegram_id": cell(tg_col),
                    "active": cell(active_col),
                    "role": cell(role_col),
                    "manager": cell(manager_col),
                }
    except Exception as e:
        logger.exception(e)

    return None


def is_manager(telegram_id: int) -> bool:
    profile = get_employee_profile_by_tg(telegram_id)
    if not profile:
        return False
    return profile.get("role", "").strip().lower() == "керівник"


def menu_keyboard(telegram_id: Optional[int] = None):
    buttons = [
        [
            button("🆕 Нові штрафи", "new|f"),
            button("⚠️ Нові попередження", "new|w"),
        ],
        [button("📂 Штрафи по категоріях", "months|f")],
        [button("📁 Попередження по категоріях", "months|w")],
    ]

    if telegram_id and is_manager(telegram_id):
        buttons.append([button("👑 Кабінет керівника", "mgr")])

    return inline_keyboard(buttons)


def get_manager_employees(manager_name: str) -> List[str]:
    try:
        values = ws(SHEET_EMPLOYEES).get_all_values()
        if not values:
            return []

        headers = values[0]
        name_col = find_col(headers, ["Співробітник", "ПІБ", "Працівник", "Ім'я", "ПІБ співробітника"])
        manager_col = find_col(headers, ["Керівник", "Куратор", "Manager"])
        active_col = find_col(headers, ["Активний", "Активна", "Active"])

        if name_col is None or manager_col is None:
            return []

        result = []
        for row in values[1:]:
            def cell(col):
                return normalize(row[col]) if col is not None and len(row) > col else ""

            active = cell(active_col).lower()
            if cell(manager_col) == manager_name and active in ["так", "", "yes", "true"]:
                name = cell(name_col)
                if name:
                    result.append(name)

        return result
    except Exception as e:
        logger.exception(e)
        return []


def get_team_records(sheet_name: str, employee_names: List[str]) -> List[Dict[str, Any]]:
    if not employee_names:
        return []

    values = ws(sheet_name).get_all_values()
    if not values:
        return []

    headers = values[0]
    employee_col = find_col(headers, ["Співробітник", "ПІБ", "Працівник", "Менеджер"])

    if employee_col is None:
        return []

    employee_set = set(employee_names)
    result = []

    for row_index, row in enumerate(values[1:], start=2):
        employee = normalize(row[employee_col]) if len(row) > employee_col else ""
        if employee in employee_set:
            result.append({"row_index": row_index, "headers": headers, "row": row})

    return result


def amount_to_number(value: Any) -> float:
    text = normalize(value).replace(" ", "").replace(",", ".")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def manager_keyboard():
    return inline_keyboard([
        [button("📊 Підсумок по моїх людях", "mgrsum")],
        [button("💸 Штрафи моїх людей", "mgrrec|f|0")],
        [button("⚠️ Попередження моїх людей", "mgrrec|w|0")],
        [button("👥 Список моїх працівників", "mgrlist")],
        [button("⬅️ Назад в меню", "menu")],
    ])


def show_manager_cabinet(chat_id: int, message_id: int, telegram_id: int):
    if not is_manager(telegram_id):
        edit_message(chat_id, message_id, "У вас немає доступу до кабінету керівника.", back_menu_keyboard())
        return

    edit_message(chat_id, message_id, "👑 Кабінет керівника\n\nОберіть розділ:", manager_keyboard())


def show_manager_list(chat_id: int, message_id: int, telegram_id: int):
    profile = get_employee_profile_by_tg(telegram_id)
    if not profile or not is_manager(telegram_id):
        edit_message(chat_id, message_id, "У вас немає доступу до кабінету керівника.", back_menu_keyboard())
        return

    employees = get_manager_employees(profile["name"])
    if not employees:
        text = "👥 Ваші працівники\n\nПрацівників не знайдено. Перевірте колонку «Керівник» у вкладці «Працівники»."
    else:
        lines = ["👥 Ваші працівники", ""]
        for i, emp in enumerate(employees, start=1):
            lines.append(f"{i}. {emp}")
        text = "\n".join(lines)

    edit_message(chat_id, message_id, text, inline_keyboard([
        [button("⬅️ До кабінету керівника", "mgr")],
        [button("⬅️ Назад в меню", "menu")],
    ]))


def show_manager_summary(chat_id: int, message_id: int, telegram_id: int):
    profile = get_employee_profile_by_tg(telegram_id)
    if not profile or not is_manager(telegram_id):
        edit_message(chat_id, message_id, "У вас немає доступу до кабінету керівника.", back_menu_keyboard())
        return

    employees = get_manager_employees(profile["name"])
    if not employees:
        edit_message(chat_id, message_id, "📊 Підсумок\n\nПрацівників не знайдено.", inline_keyboard([
            [button("⬅️ До кабінету керівника", "mgr")],
            [button("⬅️ Назад в меню", "menu")],
        ]))
        return

    fine_records = get_team_records(SHEET_FINES, employees)
    warning_records = get_team_records(SHEET_WARNINGS, employees)

    summary = {emp: {"fine_count": 0, "fine_sum": 0.0, "warning_count": 0} for emp in employees}

    for rec in fine_records:
        emp = get_value(rec, ["Співробітник", "ПІБ", "Працівник", "Менеджер"])
        if emp in summary:
            summary[emp]["fine_count"] += 1
            summary[emp]["fine_sum"] += amount_to_number(get_value(rec, ["Сума", "Штраф", "Сума штрафу"]))

    for rec in warning_records:
        emp = get_value(rec, ["Співробітник", "ПІБ", "Працівник", "Менеджер"])
        if emp in summary:
            summary[emp]["warning_count"] += 1

    lines = ["📊 Підсумок по моїх людях", ""]
    for emp in employees:
        item = summary[emp]
        fine_sum = int(item["fine_sum"]) if item["fine_sum"].is_integer() else round(item["fine_sum"], 2)
        lines.append(f"👤 {emp}")
        lines.append(f"💸 Штрафів: {item['fine_count']} / {fine_sum} грн")
        lines.append(f"⚠️ Попереджень: {item['warning_count']}")
        lines.append("")

    edit_message(chat_id, message_id, "\n".join(lines).strip(), inline_keyboard([
        [button("⬅️ До кабінету керівника", "mgr")],
        [button("⬅️ Назад в меню", "menu")],
    ]))


def show_manager_records(chat_id: int, message_id: int, telegram_id: int, code: str, index: int = 0):
    profile = get_employee_profile_by_tg(telegram_id)
    if not profile or not is_manager(telegram_id):
        edit_message(chat_id, message_id, "У вас немає доступу до кабінету керівника.", back_menu_keyboard())
        return

    employees = get_manager_employees(profile["name"])
    sheet_name = sheet_by_code(code)
    records = get_team_records(sheet_name, employees)
    title = "💸 Штрафи моїх людей" if code == "f" else "⚠️ Попередження моїх людей"

    if not records:
        edit_message(chat_id, message_id, title + "\n\nЗаписів не знайдено.", inline_keyboard([
            [button("⬅️ До кабінету керівника", "mgr")],
            [button("⬅️ Назад в меню", "menu")],
        ]))
        return

    index = max(0, min(index, len(records) - 1))
    text = build_record_text(title, records[index], index, len(records), sheet_name)

    buttons = []
    nav = []
    if index > 0:
        nav.append(button("⬅️ Назад", f"mgrrec|{code}|{index - 1}"))
    if index < len(records) - 1:
        nav.append(button("➡️ Далі", f"mgrrec|{code}|{index + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([button("⬅️ До кабінету керівника", "mgr")])
    buttons.append([button("⬅️ Назад в меню", "menu")])

    edit_message(chat_id, message_id, text, inline_keyboard(buttons))


def get_fixation_date_col(headers: List[str]) -> Optional[int]:
    return find_col(headers, [
        "Дата фіксації ліда",
        "Дата фиксации лида",
        "Дата фіксації",
        "Дата фиксации",
        "Дата порушення",
        "Дата фіксаці",
    ])


def get_records(
    sheet_name: str,
    telegram_id: int,
    only_new: bool = True,
    category: Optional[str] = None,
    month_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    employee_name = get_employee_name_by_tg(telegram_id)

    values = ws(sheet_name).get_all_values()
    if not values:
        return []

    headers = values[0]

    employee_col = find_col(headers, ["Співробітник", "ПІБ", "Працівник", "Менеджер"])
    tg_col = find_col(headers, ["Telegram ID", "Телеграм ID", "telegram_id", "tg id", "TG ID"])
    viewed_col = find_col(headers, ["Переглянуто", "Переглянутий", "Viewed", "Статус"])
    category_col = find_col(headers, ["Категорія", "Категория", "Тип"])
    fixation_date_col = get_fixation_date_col(headers)

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

        if month_key:
            if fixation_date_col is None:
                continue
            row_month_key = month_key_from_date(cell(fixation_date_col))
            if row_month_key != month_key:
                continue

        result.append({"row_index": row_index, "headers": headers, "row": row})

    return result


def get_months(sheet_name: str, telegram_id: int) -> List[str]:
    values = ws(sheet_name).get_all_values()
    if not values:
        return []

    headers = values[0]
    employee_name = get_employee_name_by_tg(telegram_id)

    employee_col = find_col(headers, ["Співробітник", "ПІБ", "Працівник", "Менеджер"])
    tg_col = find_col(headers, ["Telegram ID", "Телеграм ID", "telegram_id", "tg id", "TG ID"])
    fixation_date_col = get_fixation_date_col(headers)

    if fixation_date_col is None:
        return []

    months = set()

    for row in values[1:]:
        def cell(col):
            return normalize(row[col]) if col is not None and len(row) > col else ""

        allowed = False

        if tg_col is not None and cell(tg_col) == str(telegram_id):
            allowed = True

        if not allowed and employee_name and employee_col is not None and cell(employee_col) == employee_name:
            allowed = True

        if not allowed:
            continue

        key = month_key_from_date(cell(fixation_date_col))
        if key:
            months.add(key)

    return sorted(months, reverse=True)


def get_categories(sheet_name: str, telegram_id: int, month_key: str) -> List[str]:
    records = get_records(sheet_name, telegram_id, only_new=False, month_key=month_key)
    categories = set()

    for rec in records:
        category_col = find_col(rec["headers"], ["Категорія", "Категория", "Тип"])
        if category_col is not None and len(rec["row"]) > category_col:
            category = normalize(rec["row"][category_col])
            if category:
                categories.add(category)

    return sorted(categories)


def get_value(rec: Dict[str, Any], variants: List[str]) -> str:
    col = find_col(rec["headers"], variants)
    if col is None or len(rec["row"]) <= col:
        return "-"
    return normalize(rec["row"][col]) or "-"


def build_record_text(title: str, rec: Dict[str, Any], index: int, total: int, sheet_name: str) -> str:
    lines = [
        title,
        "",
        f"📄 Запис {index + 1} з {total}",
        "",
        f"🆔 ID: {get_value(rec, ['ID', 'ID порушення', '№'])}",
        f"📅 Дата надходження: {get_value(rec, ['Дата надходження', 'Дата'])}",
        f"📅 Дата фіксації ліда: {get_value(rec, ['Дата фіксації ліда', 'Дата фиксации лида', 'Дата фіксації', 'Дата порушення'])}",
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


def show_months(chat_id: int, message_id: int, telegram_id: int, code: str):
    sheet_name = sheet_by_code(code)
    months = get_months(sheet_name, telegram_id)
    title = section_title(code)

    if not months:
        edit_message(
            chat_id,
            message_id,
            title + "\n\nМісяців не знайдено. Перевір колонку «Дата фіксації ліда».",
            back_menu_keyboard()
        )
        return

    buttons = [[button("🗓 " + month_label(m), f"cats|{code}|{m}")] for m in months]
    buttons.append([button("⬅️ Назад в меню", "menu")])

    edit_message(chat_id, message_id, title + "\n\n🗓 Оберіть місяць:", inline_keyboard(buttons))


def show_categories(chat_id: int, message_id: int, telegram_id: int, code: str, month_key: str):
    sheet_name = sheet_by_code(code)
    categories = get_categories(sheet_name, telegram_id, month_key)
    title = section_title(code)

    if not categories:
        edit_message(chat_id, message_id, title + f"\n🗓 {month_label(month_key)}\n\nКатегорій за цей місяць немає.", inline_keyboard([
            [button("⬅️ До місяців", f"months|{code}")],
            [button("⬅️ Назад в меню", "menu")]
        ]))
        return

    buttons = []
    for i, cat in enumerate(categories):
        buttons.append([button("📌 " + cat, f"catopen|{code}|{month_key}|{i}")])

    buttons.append([button("⬅️ До місяців", f"months|{code}")])
    buttons.append([button("⬅️ Назад в меню", "menu")])

    edit_message(chat_id, message_id, title + f"\n🗓 {month_label(month_key)}\n\n📂 Оберіть категорію:", inline_keyboard(buttons))


def show_records(
    chat_id: int,
    message_id: int,
    telegram_id: int,
    code: str,
    index: int = 0,
    category_index: Optional[int] = None,
    month_key: Optional[str] = None,
    only_new: bool = True,
):
    sheet_name = sheet_by_code(code)
    category = None

    if category_index is not None and month_key is not None:
        categories = get_categories(sheet_name, telegram_id, month_key)
        if category_index < 0 or category_index >= len(categories):
            edit_message(chat_id, message_id, "Категорію не знайдено.", back_menu_keyboard())
            return
        category = categories[category_index]

    records = get_records(sheet_name, telegram_id, only_new=only_new, category=category, month_key=month_key)

    if category and month_key:
        title = f"{section_title(code)}\n🗓 {month_label(month_key)}\n📌 {category}"
    else:
        title = new_title(code)

    if not records:
        edit_message(chat_id, message_id, title + "\n\n✅ Записів немає.", back_menu_keyboard())
        return

    index = max(0, min(index, len(records) - 1))
    rec = records[index]
    text = build_record_text(title, rec, index, len(records), sheet_name)

    buttons = []
    nav = []

    if category_index is not None and month_key is not None:
        page_action = "catpage"
        extra = f"|{month_key}|{category_index}"
    else:
        page_action = "page"
        extra = ""

    if index > 0:
        nav.append(button("⬅️ Назад", f"{page_action}|{code}|{index - 1}{extra}"))
    if index < len(records) - 1:
        nav.append(button("➡️ Далі", f"{page_action}|{code}|{index + 1}{extra}"))

    if nav:
        buttons.append(nav)

    if only_new and category_index is None:
        buttons.append([button("✅ Переглянуто", f"viewed|{code}|{rec['row_index']}")])

    if category_index is not None and month_key is not None:
        buttons.append([button("⬅️ До категорій", f"cats|{code}|{month_key}")])
        buttons.append([button("⬅️ До місяців", f"months|{code}")])

    buttons.append([button("⬅️ Назад в меню", "menu")])

    edit_message(chat_id, message_id, text, inline_keyboard(buttons))


def mark_viewed(sheet_name: str, row_index: int):
    worksheet = ws(sheet_name)
    headers = worksheet.row_values(1)
    viewed_col = find_col(headers, ["Переглянуто", "Переглянутий", "Viewed", "Статус"])

    if viewed_col is None:
        return

    worksheet.update_cell(row_index, viewed_col + 1, "Так")


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
        edit_message(chat_id, message_id, "📋 Меню\n\nОберіть розділ:", menu_keyboard(telegram_id))
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

    if action == "months":
        show_months(chat_id, message_id, telegram_id, parts[1])
        return

    if action == "cats":
        show_categories(chat_id, message_id, telegram_id, parts[1], parts[2])
        return

    if action == "catopen":
        code = parts[1]
        month_key = parts[2]
        category_index = int(parts[3])
        show_records(chat_id, message_id, telegram_id, code, 0, category_index=category_index, month_key=month_key, only_new=False)
        return

    if action == "catpage":
        code = parts[1]
        index = int(parts[2])
        month_key = parts[3]
        category_index = int(parts[4])
        show_records(chat_id, message_id, telegram_id, code, index, category_index=category_index, month_key=month_key, only_new=False)
        return

    if action == "mgr":
        show_manager_cabinet(chat_id, message_id, telegram_id)
        return

    if action == "mgrlist":
        show_manager_list(chat_id, message_id, telegram_id)
        return

    if action == "mgrsum":
        show_manager_summary(chat_id, message_id, telegram_id)
        return

    if action == "mgrrec":
        code = parts[1]
        index = int(parts[2])
        show_manager_records(chat_id, message_id, telegram_id, code, index)
        return


def handle_message(message: Dict[str, Any]):
    chat_id = message["chat"]["id"]
    text = message.get("text", "")

    if text in ["/start", "/menu", "старт", "Старт"]:
        send_message(chat_id, "📋 Меню\n\nОберіть розділ:", menu_keyboard(message.get("from", {}).get("id")))
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
