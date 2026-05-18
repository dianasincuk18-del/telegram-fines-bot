import os
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import gspread
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

SHEET_FINES = os.environ.get("SHEET_FINES", "Штрафи")
SHEET_WARNINGS = os.environ.get("SHEET_WARNINGS", "Попередження")

TZ_LABEL = "Europe/Kyiv"


def tg(method: str, payload: Dict[str, Any]):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    response = requests.post(url, json=payload, timeout=20)

    try:
        data = response.json()
    except Exception:
        data = {"ok": False, "text": response.text}

    logger.info("Telegram %s response: %s", method, data)
    return data


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


def get_cell(row: List[str], col: Optional[int]) -> str:
    if col is None or len(row) <= col:
        return ""
    return normalize(row[col])


def now_text() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M:%S")


def send_notification(chat_id: str, text: str, callback_data: str):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "Відкрити в боті", "callback_data": callback_data}]
            ]
        }
    }
    return tg("sendMessage", payload)


def process_sheet(sheet_name: str, kind: str):
    worksheet = ws(sheet_name)
    values = worksheet.get_all_values()

    if not values:
        logger.info("Sheet %s is empty", sheet_name)
        return

    headers = values[0]

    status_col = find_col(headers, ["Статус Telegram", "Статус телеграм", "Telegram status"])
    sent_at_col = find_col(headers, ["Дата надсилання", "Дата отправки", "Sent at"])
    viewed_col = find_col(headers, ["Переглянуто", "Просмотрено", "Viewed"])
    telegram_col = find_col(headers, ["Telegram ID", "Телеграм ID", "telegram_id", "tg id", "TG ID"])

    fixation_col = find_col(headers, ["Дата фіксації ліда", "Дата фіксації", "Дата фиксации лида", "Дата фиксации", "Дата порушення"])
    employee_col = find_col(headers, ["Співробітник", "ПІБ", "Працівник", "Менеджер"])
    category_col = find_col(headers, ["Категорія", "Категория", "Тип"])
    amount_col = find_col(headers, ["Сума", "Штраф", "Сума штрафу"])

    if status_col is None or sent_at_col is None or telegram_col is None:
        logger.error("Missing required columns in %s", sheet_name)
        return

    sent_count = 0

    for row_index, row in enumerate(values[1:], start=2):
        status = get_cell(row, status_col).lower()
        telegram_id = get_cell(row, telegram_col)

        if not telegram_id:
            continue

        if status not in ["не надіслано", "не отправлено", ""]:
            continue

        fixation_date = get_cell(row, fixation_col) or "-"
        employee = get_cell(row, employee_col) or "-"
        category = get_cell(row, category_col) or "-"

        if kind == "fine":
            amount = get_cell(row, amount_col) or "-"
            text = (
                "🔔 У вас новий штраф\n\n"
                f"📅 Дата фіксації: {fixation_date}\n"
                f"📂 Категорія: {category}\n"
                f"💰 Сума: {amount} грн\n\n"
                "Щоб переглянути деталі, відкрийте розділ:\n"
                "🆕 Нові штрафи"
            )
            callback_data = "new|f"
        else:
            text = (
                "🔔 У вас нове попередження\n\n"
                f"📅 Дата фіксації: {fixation_date}\n"
                f"📂 Категорія: {category}\n\n"
                "Щоб переглянути деталі, відкрийте розділ:\n"
                "⚠️ Нові попередження"
            )
            callback_data = "new|w"

        result = send_notification(telegram_id, text, callback_data)

        if result.get("ok"):
            worksheet.update_cell(row_index, status_col + 1, "Надіслано")
            worksheet.update_cell(row_index, sent_at_col + 1, now_text())

            if viewed_col is not None and not get_cell(row, viewed_col):
                worksheet.update_cell(row_index, viewed_col + 1, "Ні")

            sent_count += 1
        else:
            logger.warning("Failed to send row %s in %s: %s", row_index, sheet_name, result)

    logger.info("Sent %s notifications from %s", sent_count, sheet_name)


def main():
    process_sheet(SHEET_FINES, "fine")
    process_sheet(SHEET_WARNINGS, "warning")


if __name__ == "__main__":
    main()
