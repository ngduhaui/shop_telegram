import asyncio
import io
import json
import os
import shlex
import time
import urllib.request
from urllib.parse import quote

from dotenv import load_dotenv
import requests
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# ===== Cau hinh (sua o day hoac .env) =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "THAY_TOKEN_VAO_DAY")
SEPAY_ACC = os.getenv("SEPAY_ACC", "")       # so tai khoan / ma VA
SEPAY_BANK = os.getenv("SEPAY_BANK", "BIDV") # bank id theo vietqr.app/banks.json
SEPAY_HOLDER = os.getenv("SEPAY_HOLDER", "") # chu tai khoan, khong dau
SEPAY_STORE = os.getenv("SEPAY_STORE", "")   # ten cua hang
SEPAY_TOKEN = os.getenv("SEPAY_TOKEN", "")   # API token tu my.sepay.vn
PAY_WAIT_MIN = 10  # so phut cho thanh toan truoc khi xoa QR
ADMIN_IDS = set(int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x)
PRODUCTS_FILE = "products.json"

# San pham: ma -> {name, price, stock}. Load tu products.json.
PRODUCTS: dict = {}


def load_products() -> None:
    try:
        with open(PRODUCTS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}  # chưa có file, /addsp để tạo
    for code, v in raw.items():
        if isinstance(v, list):  # format cu [ten, gia]
            v = {"name": v[0], "price": v[1], "stock": 0, "cat": "Khác"}
        elif isinstance(v, dict) and "name" not in v:
            # format lồng: {danh muc: {ma: san pham}}
            for sub, p in v.items():
                _norm_product(sub, p, code)
            continue
        _norm_product(code, v, v.get("cat", "Khác"))


def _norm_product(code, v, cat) -> None:
    v.setdefault("stock", 0)
    v.setdefault("cat", cat)
    PRODUCTS[code] = v


def save_products() -> None:
    grouped: dict = {}
    for code, p in sorted(PRODUCTS.items()):
        grouped.setdefault(p.get("cat", "Khác"), {})[code] = p
    with open(PRODUCTS_FILE, "w", encoding="utf-8") as f:
        json.dump(grouped, f, ensure_ascii=False, indent=2)


load_products()

# Don hang dang cho thanh toan: message_id -> {chat_id, total, note, deadline}
_pending = {}


def qr_url(amount: int, note: str) -> str:
    """QR thanh toan tu vietqr.app; des = noi dung chuyen khoan (id user)."""
    return (
        f"https://vietqr.app/img?acc={SEPAY_ACC}&bank={SEPAY_BANK}"
        f"&amount={amount}&des={quote(note)}&template=compact"
        f"&showinfo=true&holder={quote(SEPAY_HOLDER)}"
    )


def cat_kb() -> InlineKeyboardMarkup:
    cats = sorted({p["cat"] for p in PRODUCTS.values() if p["stock"] > 0})
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(c, callback_data=f"cat:{c}")] for c in cats]
    )


def product_kb(cat: str) -> InlineKeyboardMarkup:
    rows = []
    for code, p in PRODUCTS.items():
        if p["cat"] != cat or p["stock"] <= 0:
            continue  # khac danh muc / het hang -> an
        rows.append(
            [
                InlineKeyboardButton(
                    f"{p['name']} - {p['price']:,}đ (còn {p['stock']})",
                    callback_data=f"buy:{code}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="cat:__back__")])
    return InlineKeyboardMarkup(rows)


def qty_kb(cat: str = "") -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(str(n), callback_data=f"qty:{n}") for n in range(1, 6)],
        [InlineKeyboardButton("✏️ Nhập số lượng khác", callback_data="qty:custom")],
    ]
    if cat:
        rows.append([InlineKeyboardButton("⬅️ Quay lại", callback_data=f"cat:{cat}")])
    return InlineKeyboardMarkup(rows)


def is_admin(update: Update) -> bool:
    return update.effective_user.id in ADMIN_IDS


async def market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not any(p["stock"] > 0 for p in PRODUCTS.values()):
        await update.message.reply_text("Hiện hết hàng, quay lại sau nhé.")
        return
    await update.message.reply_text("🛒 Chọn danh mục:", reply_markup=cat_kb())


# ===== Admin: quan ly san pham =====

async def addsp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addsp <ma> <ten> <gia> [<so_luong>] [cat:<ten>] hoac /addsp + file json."""
    if not is_admin(update):
        return
    if update.message.document:
        await addsp_file(update, context)
        return
    parts = shlex.split(update.message.text)
    if len(parts) < 4:
        await update.message.reply_text("Cú pháp: /addsp <ma> <ten> <gia> [<so_luong>]")
        return
    code, name, price = parts[1], parts[2], parts[3]
    try:
        price = int(price)
    except ValueError:
        await update.message.reply_text("Giá phải là số.")
        return
    old = PRODUCTS.get(code, {})
    try:
        stock = int(parts[4]) if len(parts) > 4 else old.get("stock", 0)
    except ValueError:
        await update.message.reply_text("Số lượng phải là số.")
        return
    cat = "Khác"
    t = parts[5] if len(parts) > 5 else ""
    if t.startswith("cat:"):
        cat = t[4:]
    PRODUCTS[code] = {"name": name, "price": price, "stock": stock, "cat": cat}
    save_products()
    await update.message.reply_text(f"✅ Đã lưu: {name} - {price:,}đ (còn {stock}) — {cat}")


async def addsp_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/addsp + file json: ham import hang loat san pham."""
    doc = update.message.document
    if not doc.file_name or not doc.file_name.endswith(".json"):
        await update.message.reply_text("Gửi file .json danh sách sản phẩm.")
        return
    f = await doc.get_file()
    data = io.BytesIO()
    await f.download_to_memory(data)
    try:
        raw = json.loads(data.getvalue().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        await update.message.reply_text("File không phải JSON hợp lệ.")
        return
    if not isinstance(raw, dict):
        await update.message.reply_text("JSON phải là object {ma: {name, price, stock, cat}}")
        return
    n = 0
    for code, v in raw.items():
        if isinstance(v, list):  # format cu [ten, gia]
            v = {"name": v[0], "price": v[1], "stock": 0, "cat": "Khác"}
        v.setdefault("stock", 0)
        v.setdefault("cat", "Khác")
        PRODUCTS[code] = v
        n += 1
    save_products()
    await update.message.reply_text(f"✅ Đã nhập {n} sản phẩm từ file.")


async def delsp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/delsp <ma> — xoa san pham."""
    if not is_admin(update):
        return
    code = update.message.text.split(maxsplit=2)[-1]
    if code not in PRODUCTS:
        await update.message.reply_text("Không có mã này.")
        return
    del PRODUCTS[code]
    save_products()
    await update.message.reply_text(f"✅ Đã xóa {code}.")


async def listsp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/listsp — danh sach san pham."""
    if not is_admin(update):
        return
    if not PRODUCTS:
        await update.message.reply_text("Chưa có sản phẩm.")
        return
    await update.message.reply_text(
        "\n".join(
            f"{code}: {p['name']} - {p['price']:,}đ (còn {p['stock']})"
            for code, p in PRODUCTS.items()
        )
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Chào mừng! Gõ /market để mua account ChatGPT Plus.")


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    code = q.data.split(":", 1)[1]
    p = PRODUCTS[code]
    if p["stock"] < 1:
        await q.answer("Sản phẩm đã hết hàng!")
        return
    context.user_data["product"] = code
    context.user_data["cat"] = p["cat"]
    kb = qty_kb(cat=p["cat"])
    await q.edit_message_text(
        f"{p['name']} - {p['price']:,}đ (còn {p['stock']})\nChọn số lượng:",
        reply_markup=kb,
    )


async def choose_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bam danh muc -> hien san pham trong danh muc."""
    q = update.callback_query
    await q.answer()
    cat = q.data.split(":", 1)[1]
    if cat == "__back__":
        await q.edit_message_text("🛒 Chọn danh mục:", reply_markup=cat_kb())
        return
    await q.edit_message_text(f"📦 {cat} — chọn gói:", reply_markup=product_kb(cat))


async def qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data.split(":", 1)[1]
    code = context.user_data.get("product")
    if data == "custom":
        if not code:
            await q.edit_message_text("Hết hạn, /market lại.")
            return
        context.user_data["qty_prompt_mid"] = q.message.message_id
        await q.edit_message_text("Gõ số lượng cần mua (vd: 3):")
        return
    n = int(data)
    if not code:
        await q.edit_message_text("Hết hạn, /market lại.")
        return
    p = PRODUCTS[code]
    if n < 1 or n > p["stock"]:
        await q.edit_message_text(
            f"Số lượng phải từ 1 đến {p['stock']} (còn hàng). Chọn lại:",
            reply_markup=qty_kb(cat=context.user_data.get("cat", "")),
        )
        return
    total = p["price"] * n
    note = f"{update.effective_user.id}K{int(time.time()) % 10**6}"  # ma don, unique moi lan
    try:
        msg = await q.message.reply_photo(
            qr_url(total, note),
            caption=(
                f"🛒 {p['name']} x{n}\n"
                f"💰 Tổng: {total:,}đ\n"
                f"📝 Nội dung: {note}\n"
                f"⏳ QR hết hạn sau {PAY_WAIT_MIN} phút."
            ),
            parse_mode="Markdown",
        )
        await q.message.delete()  # xoa menu chon san pham
    except Exception as e:
        await q.edit_message_text(f"Lỗi tạo QR: {e}")
        return
    p["stock"] -= n
    save_products()
    _pending[msg.message_id] = {
        "chat_id": update.effective_chat.id,
        "code": code,
        "qty": n,
        "total": total,
        "note": note,
        "deadline": time.time() + PAY_WAIT_MIN * 60,
    }


async def qty_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tin nhan so luong: tao QR tu so luong nhap, giong luong qty."""
    code = context.user_data.pop("product", None)
    if not code:
        return  # khong dang trong luong nhap qty
    text = update.message.text.strip()
    if not text.isdigit():
        context.user_data["product"] = code
        await update.message.reply_text("Số lượng phải là số nguyên (vd: 3). Nhập lại:")
        return
    n = int(text)
    p = PRODUCTS[code]
    if n < 1 or n > p["stock"]:
        context.user_data["product"] = code
        await update.message.reply_text(
            f"Số lượng phải từ 1 đến {p['stock']} (còn hàng). Nhập lại:"
        )
        return
    total = p["price"] * n
    note = f"{update.effective_user.id}K{int(time.time()) % 10**6}"  # moi don 1 note
    try:
        msg = await context.bot.send_photo(
            update.effective_chat.id,
            qr_url(total, note),
            caption=(
                f"🛒 {p['name']} x{n}\n"
                f"💰 Tổng: {total:,}đ\n"
                f"📝 Nội dung: {note}\n"
                f"⏳ QR hết hạn sau {PAY_WAIT_MIN} phút."
            ),
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"Lỗi tạo QR: {e}")
        return
    p["stock"] -= n
    save_products()
    # xoa tin nhap so luong va tin prompt
    try:
        await update.message.delete()
    except Exception:
        pass
    mid = context.user_data.pop("qty_prompt_mid", None)
    if mid:
        try:
            await context.bot.delete_message(update.effective_chat.id, mid)
        except Exception:
            pass
    _pending[msg.message_id] = {
        "chat_id": update.effective_chat.id,
        "code": code,
        "qty": n,
        "total": total,
        "note": note,
        "deadline": time.time() + PAY_WAIT_MIN * 60,
    }


def fetch_transactions() -> list:
    """Giao dich den (transfer_type=in) tu SePay API."""
    if not SEPAY_TOKEN:
        return []
    url = (
        "https://userapi.sepay.vn/v2/transactions?transfer_type=in"
        "&per_page=100&transaction_date_sort=desc"
    )
    try:
        with requests.get(url, headers={"Authorization": f"Bearer {SEPAY_TOKEN}"}, timeout=10) as resp:
            return [
                t for t in resp.json().get("data", [])
                if t.get("transfer_type") == "in"
            ]
    except Exception:
        return []  # loi mang, de vong sau


async def check_payments(bot: Bot) -> None:
    """Quet: don nao giong noi dung + dung so tien -> xoa QR, gui thank you."""
    for mid, order in list(_pending.items()):
        paid = any(
            t.get("transaction_content", "").strip() == order["note"]
            and t.get("amount_in") == order["total"]
            for t in fetch_transactions()
        )
        if not paid:
            continue  # chua thanh toan, con trong thoi gian doi
        try:
            await bot.delete_message(chat_id=order["chat_id"], message_id=mid)
        except Exception:
            pass
        await bot.send_message(
            order["chat_id"],
            f"✅ Bạn đã thanh toán thành công! Cảm ơn bạn đã mua hàng 🎉\n"
            f"Đơn {order['total']:,}đ của bạn đang được xử lý, "
            f"account sẽ được gửi ngay sau khi xác nhận.",
        )
        del _pending[mid]


async def expire_orders(bot: Bot) -> None:
    """Sau PAY_WAIT_MIN: xoa QR het han, tra lai stock."""
    for mid, order in list(_pending.items()):
        if order["deadline"] > time.time():
            continue
        try:
            await bot.delete_message(chat_id=order["chat_id"], message_id=mid)
        except Exception:
            pass
        await bot.send_message(
            order["chat_id"],
            f"⏰ QR đã hết hạn sau {PAY_WAIT_MIN} phút chưa thấy thanh toán. "
            f"Gõ /market để đặt lại.",
        )
        code = order.get("code")
        if code in PRODUCTS:
            PRODUCTS[code]["stock"] += order.get("qty", 0)
            save_products()
        del _pending[mid]


async def poll_loop(bot: Bot) -> None:
    """Moi 30s: check thanh toan + don het han."""
    while True:
        try:
            await check_payments(bot)
            await expire_orders(bot)
        except Exception:
            pass
        await asyncio.sleep(30)


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    async def post_init(application: Application) -> None:
        asyncio.get_running_loop().create_task(poll_loop(application.bot))

    app.post_init = post_init
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("market", market))
    app.add_handler(CommandHandler("addsp", addsp))
    app.add_handler(CommandHandler("delsp", delsp))
    app.add_handler(CommandHandler("listsp", listsp))
    app.add_handler(CallbackQueryHandler(choose_cat, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(qty, pattern=r"^qty:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, qty_text))
    print("Bot dang chay. Nhan Ctrl+C de dung.")
    app.run_polling()


if __name__ == "__main__":
    main()