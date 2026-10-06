# -*- coding: utf-8 -*-
"""
TRADENBOT — гарант-бот для сделок с NFT-подарками в Telegram.

Схема работы:
  1. Участник создаёт сделку (подарок, сумма, продавец, покупатель).
  2. Покупатель переводит деньги владельцу бота (@ws3xx3).
  3. Владелец жмёт кнопку «✅ Подтвердить оплату».
  4. Продавцу в чат с ботом приходит ЧЕК об оплате.
  5. Владелец завершает сделку после передачи NFT-подарка.
"""

import asyncio
import html
import json
import logging
import os
import random
import string
import urllib.request
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ─────────────────────────── НАСТРОЙКИ ───────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "ws3xx3").lstrip("@").lower()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
PUBLIC_URL = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("PUBLIC_URL") or "").rstrip("/")
PORT = int(os.getenv("PORT", "8443"))

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE = DATA_DIR / "users.json"
DEALS_FILE = DATA_DIR / "deals.json"

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("tradenbot")

ITEM, AMOUNT, SELLER, BUYER = range(4)

STATUS = {
    "pending": ("🕐", "Ожидает оплаты"),
    "paid": ("💰", "Оплачена — деньги у гаранта"),
    "done": ("✅", "Завершена"),
    "rejected": ("❌", "Отклонена"),
}

# ─────────────────────────── ХРАНИЛИЩЕ ───────────────────────────


def load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("Не удалось прочитать %s", path)
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


users: dict = load_json(USERS_FILE, {})
deals: dict = load_json(DEALS_FILE, {})


def save_users():
    save_json(USERS_FILE, users)


def save_deals():
    save_json(DEALS_FILE, deals)


def now() -> str:
    return datetime.now().strftime("%d.%m.%Y %H:%M")


def register_user(update: Update) -> None:
    u = update.effective_user
    if not u:
        return
    rec = users.get(str(u.id), {})
    rec.update(
        {
            "id": u.id,
            "username": u.username or rec.get("username", ""),
            "first_name": u.first_name or rec.get("first_name", ""),
            "last_seen": now(),
        }
    )
    users[str(u.id)] = rec
    save_users()


def find_user_id(username: str):
    """Поиск id пользователя по @username среди тех, кто запускал бота."""
    target = username.lstrip("@").lower()
    for rec in users.values():
        if str(rec.get("username", "")).lower() == target:
            return rec.get("id")
    return None


def is_owner(user) -> bool:
    if not user:
        return False
    if OWNER_ID and user.id == OWNER_ID:
        return True
    return bool(user.username) and user.username.lower() == OWNER_USERNAME


def owner_chat_id():
    if OWNER_ID:
        return OWNER_ID
    return find_user_id(OWNER_USERNAME)


def gen_deal_id() -> str:
    while True:
        did = "T-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        if did not in deals:
            return did


def esc(text: str) -> str:
    return html.escape(text or "")


def mention(rec: dict) -> str:
    """Красивое упоминание: @username или имя."""
    if rec.get("username"):
        return f"@{rec['username']}"
    return esc(rec.get("first_name") or "Неизвестный")


# ─────────────────────────── ТЕКСТЫ / КЛАВИАТУРЫ ───────────────────────────


def main_keyboard(admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        ["🛒 Новая сделка", "📋 Мои сделки"],
        ["ℹ️ Помощь"],
    ]
    if admin:
        rows.append(["👑 Админ-панель"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


WELCOME = (
    "👋 Добро пожаловать в <b>TRADENBOT</b>!\n"
    "\n"
    "🔒 Я — <b>гарант</b> между продавцом и покупателем NFT-подарка в Telegram.\n"
    "\n"
    "Как это работает:\n"
    "1️⃣ Создайте сделку — укажите подарок, сумму, продавца и покупателя\n"
    "2️⃣ Покупатель отправляет деньги владельцу бота\n"
    "3️⃣ Владелец <b>@{owner}</b> нажимает «✅ Подтвердить оплату»\n"
    "4️⃣ Продавцу приходит <b>чек об оплате</b> прямо в чат с ботом\n"
    "5️⃣ Продавец передаёт NFT-подарок, владелец завершает сделку\n"
    "\n"
    "🛡 Владелец и гарант: <b>@{owner}</b>\n"
    "\n"
    "Выберите действие ниже 👇"
).format(owner=OWNER_USERNAME)

HELP = (
    "ℹ️ <b>Как пользоваться TRADENBOT</b>\n"
    "\n"
    "🛒 <b>Новая сделка</b> — создать сделку купли-продажи NFT-подарка.\n"
    "Бот попросит: описание подарка, сумму, @username продавца и покупателя.\n"
    "\n"
    "💰 <b>Оплата</b> — покупатель отправляет сумму сделки владельцу бота "
    "(@{owner}) удобным способом (перевод, карта, криптовалюта — как договорились).\n"
    "\n"
    "✅ <b>Подтверждение</b> — как только деньги у владельца, он жмёт "
    "кнопку «Подтвердить оплату». Сразу после этого продавцу в чат с ботом "
    "приходит <b>чек</b> — офицальный документ по сделке.\n"
    "\n"
    "➡️ <b>Завершение</b> — продавец передаёт NFT-подарок покупателю, "
    "владелец нажимает «Завершить сделку».\n"
    "\n"
    "🆘 Команды: /start — главное меню, /cancel — отмена ввода."
).format(owner=OWNER_USERNAME)


def deal_card(deal_id: str, d: dict, title: str) -> str:
    emoji, status_text = STATUS[d["status"]]
    return (
        f"{title}\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🆔 Сделка: <b>#{deal_id}</b>\n"
        f"🎁 Подарок: <b>{esc(d['item'])}</b>\n"
        f"💵 Сумма: <b>{esc(d['amount'])}</b>\n"
        f"👤 Продавец: <b>{esc(d['seller'])}</b>\n"
        f"👤 Покупатель: <b>{esc(d['buyer'])}</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"Статус: {emoji} <b>{status_text}</b>\n"
        f"📅 Создана: {d['created']}"
        + (f"\n💳 Оплачена: {d['paid_at']}" if d.get("paid_at") else "")
        + (f"\n🏁 Завершена: {d['done_at']}" if d.get("done_at") else "")
    )


def receipt(deal_id: str, d: dict, final: bool = False) -> str:
    head = "🏁 <b>ЧЕК ЗАВЕРШЕНИЯ СДЕЛКИ</b>" if final else "🧾 <b>ЧЕК ОБ ОПЛАТЕ</b>"
    return (
        f"{head}\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🆔 Сделка: <b>#{deal_id}</b>\n"
        f"🎁 Предмет: <b>{esc(d['item'])}</b>\n"
        f"💵 Сумма: <b>{esc(d['amount'])}</b>\n"
        f"👤 Продавец: <b>{esc(d['seller'])}</b>\n"
        f"👤 Покупатель: <b>{esc(d['buyer'])}</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"✅ Оплата подтверждена гарант-ботом <b>TRADENBOT</b>\n"
        f"🛡 Гарант: <b>@{OWNER_USERNAME}</b>\n"
        f"📅 Дата подтверждения: {d.get('paid_at', d['created'])}\n"
        + (f"🏁 Дата завершения: {d.get('done_at', now())}\n" if final else "")
        + "━━━━━━━━━━━━━━━━━\n"
        "<i>Средства приняты под контролем владельца бота. "
        "Чек является подтверждением оплаты по сделке.</i>"
    )


def owner_keyboard(deal_id: str, d: dict) -> InlineKeyboardMarkup:
    rows = []
    if d["status"] == "pending":
        rows.append(
            [
                InlineKeyboardButton("✅ Подтвердить оплату", callback_data=f"pay:{deal_id}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"rej:{deal_id}"),
            ]
        )
    elif d["status"] == "paid":
        rows.append(
            [
                InlineKeyboardButton(
                    "➡️ Завершить сделку (подарок передан)", callback_data=f"done:{deal_id}"
                ),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"rej:{deal_id}"),
            ]
        )
    else:
        rows.append([InlineKeyboardButton("ℹ️ Сделка закрыта", callback_data=f"noop:{deal_id}")])
    rows.append([InlineKeyboardButton("📋 Мои сделки", callback_data="mydeals")])
    return InlineKeyboardMarkup(rows)


# ─────────────────────────── СОЗДАНИЕ СДЕЛКИ ───────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    register_user(update)
    admin = is_owner(update.effective_user)
    await update.message.reply_text(
        WELCOME, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(admin)
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    register_user(update)
    await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)


async def new_deal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    register_user(update)
    context.user_data["draft"] = {}
    await update.message.reply_text(
        "🛒 <b>Новая сделка</b>\n\n"
        "Шаг 1️⃣ из 4️⃣\n"
        "Опишите NFT-подарок, который продаётся:\n"
        "<i>Например: «NFT-подарок Telegram 2025, редкость: Epic»</i>\n\n"
        "Для отмены — /cancel",
        parse_mode=ParseMode.HTML,
    )
    return ITEM


async def got_item(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if len(text) > 500:
        await update.message.reply_text("❌ Слишком длинное описание. Сократите до 500 символов.")
        return ITEM
    context.user_data["draft"]["item"] = text
    await update.message.reply_text(
        "Шаг 2️⃣ из 4️⃣\n"
        "Укажите <b>сумму сделки</b> с валютой:\n"
        "<i>Например: 1 500 ₽ / 25 USDT</i>",
        parse_mode=ParseMode.HTML,
    )
    return AMOUNT


async def got_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if len(text) > 50:
        await update.message.reply_text("❌ Слишком длинная сумма. Пример: 1500 ₽")
        return AMOUNT
    context.user_data["draft"]["amount"] = text
    await update.message.reply_text(
        "Шаг 3️⃣ из 4️⃣\n"
        "Укажите <b>@username продавца</b> NFT-подарка:\n"
        "<i>Пример: @seller_nick</i>",
        parse_mode=ParseMode.HTML,
    )
    return SELLER


async def got_seller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.startswith("@"):
        text = "@" + text.lstrip("@")
    if len(text) < 6:
        await update.message.reply_text("❌ Некорректный @username. Попробуйте ещё раз:")
        return SELLER
    context.user_data["draft"]["seller"] = text
    await update.message.reply_text(
        "Шаг 4️⃣ из 4️⃣\n"
        "Укажите <b>@username покупателя</b> NFT-подарка:\n"
        "<i>Пример: @buyer_nick</i>",
        parse_mode=ParseMode.HTML,
    )
    return BUYER


async def got_buyer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.startswith("@"):
        text = "@" + text.lstrip("@")
    if len(text) < 6:
        await update.message.reply_text("❌ Некорректный @username. Попробуйте ещё раз:")
        return BUYER

    draft = context.user_data.get("draft", {})
    draft["buyer"] = text
    context.user_data["draft"] = draft

    kb = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Создать сделку", callback_data="create:1"),
                InlineKeyboardButton("✖️ Отмена", callback_data="create:0"),
            ]
        ]
    )
    await update.message.reply_text(
        "📋 <b>Проверьте данные сделки</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🎁 Подарок: <b>{esc(draft.get('item', '—'))}</b>\n"
        f"💵 Сумма: <b>{esc(draft.get('amount', '—'))}</b>\n"
        f"👤 Продавец: <b>{esc(draft.get('seller', '—'))}</b>\n"
        f"👤 Покупатель: <b>{esc(draft.get('buyer', '—'))}</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        "Всё верно? 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    return ConversationHandler.END


async def cb_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    draft = context.user_data.get("draft")

    if q.data == "create:0" or not draft:
        context.user_data.pop("draft", None)
        await q.edit_message_text("✖️ Создание сделки отменено.")
        return

    context.user_data.pop("draft", None)
    register_user(update)

    deal_id = gen_deal_id()
    d = {
        "creator_id": update.effective_user.id,
        "creator": f"@{update.effective_user.username}" if update.effective_user.username else update.effective_user.first_name,
        "item": draft.get("item", "—"),
        "amount": draft.get("amount", "—"),
        "seller": draft.get("seller", "—"),
        "buyer": draft.get("buyer", "—"),
        "seller_id": find_user_id(draft.get("seller", "")),
        "buyer_id": find_user_id(draft.get("buyer", "")),
        "status": "pending",
        "created": now(),
        "paid_at": None,
        "done_at": None,
    }
    deals[deal_id] = d
    save_deals()

    await q.edit_message_text(
        f"✅ Сделка <b>#{deal_id}</b> создана и ждёт оплаты!\n\n"
        f"Покупатель {esc(d['buyer'])} переводит <b>{esc(d['amount'])}</b> "
        f"гаранту @{OWNER_USERNAME}.\n"
        f"Как только деньги будут приняты, чек придёт продавцу {esc(d['seller'])}.",
        parse_mode=ParseMode.HTML,
    )

    # Уведомляем владельца — он подтверждает оплату
    oc = owner_chat_id()
    if oc:
        try:
            await context.bot.send_message(
                oc,
                deal_card(deal_id, d, "🔔 <b>НОВАЯ СДЕЛКА — ОЖИДАЕТ ОПЛАТЫ</b>")
                + f"\n\n👇 <b>Проверьте перевод от {esc(d['buyer'])} и подтвердите:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=owner_keyboard(deal_id, d),
            )
        except Exception:
            log.exception("Не удалось уведомить владельца")
    else:
        await context.bot.send_message(
            update.effective_user.id,
            f"⚠️ Владелец @{OWNER_USERNAME} ещё не запускал бота — сделка сохранена, "
            f"он увидит её в «Админ-панели».",
            parse_mode=ParseMode.HTML,
        )

    # Уведомляем стороны сделки (если они уже в боте)
    for uid, role in ((d["seller_id"], "продавец"), (d["buyer_id"], "покупатель")):
        if uid and uid not in (update.effective_user.id, oc):
            note = (
                f"🆕 Вас указали в сделке <b>#{deal_id}</b>\n"
                f"🎁 {esc(d['item'])} · 💵 {esc(d['amount'])}\n"
                f"Роль: {role}\n"
                f"Статус: 🕐 ожидает оплаты"
            )
            try:
                await context.bot.send_message(
                    uid, note, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(False)
                )
            except Exception:
                log.exception("Не удалось уведомить участника %s", uid)


# ─────────────────────────── ДЕЙСТВИЯ ГАРАНТА ───────────────────────────


async def cb_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    action, _, deal_id = q.data.partition(":")

    if action == "noop":
        await q.answer("ℹ️ Сделка уже закрыта.")
        return

    d = deals.get(deal_id)
    if not d:
        await q.answer("⚠️ Сделка не найдена.", show_alert=True)
        return

    # Подтверждение/отклонение/завершение — только для владельца
    if action in ("pay", "rej", "done"):
        if not is_owner(q.from_user):
            await q.answer("⛔ Это действие доступно только владельцу бота.", show_alert=True)
            return
        await q.answer()

        if action == "pay":
            if d["status"] != "pending":
                await q.edit_message_text(
                    deal_card(deal_id, d, "ℹ️ <b>СДЕЛКА</b>"), parse_mode=ParseMode.HTML
                )
                return
            d["status"] = "paid"
            d["paid_at"] = now()
            save_deals()

            await q.edit_message_text(
                deal_card(deal_id, d, "✅ <b>ОПЛАТА ПОДТВЕРЖДЕНА</b>")
                + "\n\nТеперь дождитесь передачи подарка и нажмите «Завершить сделку».",
                parse_mode=ParseMode.HTML,
                reply_markup=owner_keyboard(deal_id, d),
            )

            # ЧЕК продавцу в чат с ботом
            chk = receipt(deal_id, d)
            sid = d.get("seller_id") or find_user_id(d["seller"])
            sent = False
            if sid:
                try:
                    await context.bot.send_message(sid, chk, parse_mode=ParseMode.HTML)
                    sent = True
                except Exception:
                    log.exception("Не удалось отправить чек продавцу %s", sid)
            if not sent:
                await context.bot.send_message(
                    d["creator_id"],
                    f"📤 <b>Чек для продавца {esc(d['seller'])}</b> "
                    f"(он ещё не запускал бота — перешлите ему):\n\n{chk}",
                    parse_mode=ParseMode.HTML,
                )

            # Покупателю
            bid = d.get("buyer_id") or find_user_id(d["buyer"])
            if bid and bid != sid:
                try:
                    await context.bot.send_message(
                        bid,
                        f"💰 Оплата по сделке <b>#{deal_id}</b> подтверждена гарантом "
                        f"@{OWNER_USERNAME}.\n💵 Сумма: <b>{esc(d['amount'])}</b>\n"
                        "Продавец получил чек и должен передать вам NFT-подарок.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    log.exception("Не удалось уведомить покупателя")

        elif action == "rej":
            if d["status"] in ("done", "rejected"):
                await q.answer("ℹ️ Сделка уже закрыта.")
                return
            d["status"] = "rejected"
            save_deals()
            await q.edit_message_text(
                deal_card(deal_id, d, "❌ <b>ОПЛАТА ОТКЛОНЕНА</b>"), parse_mode=ParseMode.HTML
            )
            for uid in {d.get("seller_id") or find_user_id(d["seller"]),
                        d.get("buyer_id") or find_user_id(d["buyer"]),
                        d["creator_id"]}:
                if uid:
                    try:
                        await context.bot.send_message(
                            uid,
                            f"❌ Оплата по сделке <b>#{deal_id}</b> отклонена гарантом "
                            f"@{OWNER_USERNAME}.",
                            parse_mode=ParseMode.HTML,
                        )
                    except Exception:
                        pass

        elif action == "done":
            if d["status"] != "paid":
                await q.answer("⚠️ Сначала подтвердите оплату.", show_alert=True)
                return
            d["status"] = "done"
            d["done_at"] = now()
            save_deals()
            await q.edit_message_text(
                deal_card(deal_id, d, "🏁 <b>СДЕЛКА ЗАВЕРШЕНА</b>"), parse_mode=ParseMode.HTML
            )
            final = receipt(deal_id, d, final=True)
            for uid in {d.get("seller_id") or find_user_id(d["seller"]),
                        d.get("buyer_id") or find_user_id(d["buyer"])}:
                if uid:
                    try:
                        await context.bot.send_message(uid, final, parse_mode=ParseMode.HTML)
                    except Exception:
                        pass
        return

    # Просмотр сделки всеми участниками
    if action == "dview":
        await q.answer()
        await q.edit_message_text(
            deal_card(deal_id, d, "🗂 <b>СДЕЛКА</b>"),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Назад", callback_data="mydeals")]]
            ),
        )
        return


# ─────────────────────────── МЕНЮ / СПИСКИ ───────────────────────────


async def my_deals(update_or_query, context, edit: bool = False) -> None:
    if edit:
        uid = update_or_query.from_user.id
        send = update_or_query.edit_message_text
    else:
        uid = update_or_query.effective_user.id
        send = update_or_query.message.reply_text

    mine = [
        (did, d)
        for did, d in sorted(deals.items(), key=lambda kv: kv[1]["created"], reverse=True)
        if uid
        in (
            d.get("creator_id"),
            d.get("seller_id") or find_user_id(d["seller"]),
            d.get("buyer_id") or find_user_id(d["buyer"]),
        )
    ][:10]

    if not mine:
        await send(
            "📋 У вас пока нет сделок.\nНажмите «🛒 Новая сделка», чтобы создать первую!",
            reply_markup=None if edit else main_keyboard(is_owner(update_or_query.effective_user)),
        )
        return

    lines = ["📋 <b>ВАШИ СДЕЛКИ</b>\n━━━━━━━━━━━━━━━━━"]
    rows = []
    for did, d in mine:
        emoji, st = STATUS[d["status"]]
        lines.append(f"{emoji} <b>#{did}</b> · {esc(d['item'])} · {esc(d['amount'])} — {st}")
        rows.append([InlineKeyboardButton(f"{emoji} #{did} · {esc(d['item'])[:25]}", callback_data=f"dview:{did}")])
    rows.append([InlineKeyboardButton("🛒 Новая сделка", callback_data="newdeal")])
    await send("\n".join(lines), parse_mode=ParseMode.HTML,
               reply_markup=InlineKeyboardMarkup(rows) if edit else None)


async def cb_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    data = q.data
    await q.answer()

    if data == "mydeals":
        await my_deals(q, context, edit=True)
    elif data == "newdeal":
        await q.edit_message_text(
            "🛒 Внизу появилась кнопка «🛒 Новая сделка» — нажмите её, чтобы начать."
        )
    elif data == "admin":
        await q.edit_message_text(admin_text(), parse_mode=ParseMode.HTML,
                                  reply_markup=admin_keyboard())
    elif data == "aact":
        active = [(i, d) for i, d in deals.items() if d["status"] in ("pending", "paid")]
        if not active:
            await q.edit_message_text("📜 Активных сделок нет.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="admin")]]))
            return
        lines = ["📜 <b>АКТИВНЫЕ СДЕЛКИ</b>\n━━━━━━━━━━━━━━━━━"]
        rows = []
        for did, d in active[:15]:
            emoji, st = STATUS[d["status"]]
            lines.append(f"{emoji} <b>#{did}</b> · {esc(d['item'])} · {esc(d['amount'])} · "
                         f"Пр: {esc(d['seller'])} · Пк: {esc(d['buyer'])}")
            rows.append([InlineKeyboardButton(f"➡️ Открыть #{did}", callback_data=f"dview:{did}")])
        rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin")])
        await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML,
                                  reply_markup=InlineKeyboardMarkup(rows))


def admin_text() -> str:
    total = len(deals)
    paid = sum(1 for d in deals.values() if d["status"] == "paid")
    done = sum(1 for d in deals.values() if d["status"] == "done")
    pending = sum(1 for d in deals.values() if d["status"] == "pending")
    return (
        "👑 <b>АДМИН-ПАНЕЛЬ TRADENBOT</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"📊 Всего сделок: <b>{total}</b>\n"
        f"🕐 Ожидают оплаты: <b>{pending}</b>\n"
        f"💰 Оплачены: <b>{paid}</b>\n"
        f"🏁 Завершены: <b>{done}</b>\n"
        f"👥 Пользователей: <b>{len(users)}</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"🛡 Вы гарант и администратор этого бота."
    )


def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📜 Активные сделки", callback_data="aact")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin")],
        ]
    )


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    register_user(update)
    admin = is_owner(update.effective_user)
    text = (update.message.text or "").strip()

    if text in ("/start", "🏠 Меню"):
        await cmd_start(update, context)
    elif text == "📋 Мои сделки":
        await my_deals(update, context)
    elif text == "ℹ️ Помощь":
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)
    elif text == "👑 Админ-панель":
        if admin:
            await update.message.reply_text(admin_text(), parse_mode=ParseMode.HTML,
                                             reply_markup=admin_keyboard())
        else:
            await update.message.reply_text("⛔ Панель доступна только владельцу бота.")
    else:
        await update.message.reply_text(
            "🤔 Я не понял команду. Воспользуйтесь кнопками ниже 👇",
            reply_markup=main_keyboard(admin),
        )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("draft", None)
    await update.message.reply_text("✖️ Ввод отменён.", reply_markup=main_keyboard(is_owner(update.effective_user)))
    return ConversationHandler.END


def start_health_server() -> None:
    """Мини HTTP-сервер на $PORT: для хостингов, требующих открытый порт (Render и т.п.)."""
    port = os.getenv("PORT")
    if not port:
        return
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b"TRADENBOT is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    def run():
        try:
            HTTPServer(("0.0.0.0", int(port)), Handler).serve_forever()
        except Exception:
            log.exception("Health-сервер не запущен на порту %s", port)

    threading.Thread(target=run, daemon=True).start()
    log.info("Health-сервер слушает порт %s", port)


async def keepalive(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пинг самого себя — чтобы бесплатный хостинг не засыпал (webhook-режим)."""
    if PUBLIC_URL:
        try:
            urllib.request.urlopen(PUBLIC_URL, timeout=10)
        except Exception:
            pass


# ─────────────────────────── ЗАПУСК ───────────────────────────


def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN.startswith("СЮДА"):
        raise SystemExit(
            "❌ Не задан BOT_TOKEN!\n"
            "Получите токен у @BotFather и укажите его в переменной окружения BOT_TOKEN."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^🛒 Новая сделка$"), new_deal)],
        states={
            ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_item)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_amount)],
            SELLER: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_seller)],
            BUYER: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_buyer)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(cb_create, pattern=r"^create:"))
    app.add_handler(CallbackQueryHandler(cb_actions, pattern=r"^(pay|rej|done|dview|noop):"))
    app.add_handler(CallbackQueryHandler(cb_menu, pattern=r"^(mydeals|newdeal|admin|aact)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Совместимость с Python 3.14: явно создаём event loop, если его нет
    try:
        asyncio.get_event_loop()
    except Exception:
        asyncio.set_event_loop(asyncio.new_event_loop())

    if PUBLIC_URL:
        # Режим webhook (для Render / любого хостинга с публичным URL)
        if app.job_queue:
            app.job_queue.run_repeating(keepalive, interval=600, first=30)
        log.info("Запуск в режиме webhook: %s", PUBLIC_URL)
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{PUBLIC_URL}/{BOT_TOKEN}",
        )
    else:
        log.info("Запуск в режиме polling (удалённый доступ)")
        start_health_server()
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
