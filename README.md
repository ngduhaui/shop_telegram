# Bot bán account Telegram + thanh toán QR (VietQR/SePay)

Bot Telegram bán account ChatGPT/Claude/Gemini... Thanh toán qua QR VietQR, bot tự kiểm tra giao dịch đến bằng API SePay và xác nhận đơn khi khớp nội dung + số tiền.

## Tính năng

- **Danh mục 2 cấp**: danh mục (ChatGPT, Claude, Gemini...) → gói (1 tháng, 3 tháng, 1 năm) → chọn số lượng → QR.
- **Số lượng**: nút 1–5 hoặc nhập số tùy ý; kiểm tra theo tồn kho.
- **Tồn kho**: hiện số còn trên nút; hết hàng tự ẩn khỏi menu.
- **Thanh toán**: mỗi đơn có mã `nội dung` unique (user id + timestamp) → chuyển khoản đúng nội dung + đúng số tiền là bot tự xác nhận, gửi tin cảm ơn.
- **Hết hạn QR**: sau 10 phút QR bị xóa, tồn kho trả lại.
- **Admin**: thêm/sửa/xóa sản phẩm, nhập hàng loạt bằng file JSON.

## Cài đặt

```bash
# 1. Clone + cài dependencies
git clone <url>
cd Bot_telegram
pip install -r requirements.txt   # python-telegram-bot, requests, python-dotenv

# 2. Tạo .env
cp .env.example .env
#   điền BOT_TOKEN (lấy từ @BotFather), SEPAY_ACC/BANK/HOLDER,
#   SEPAY_TOKEN (lấy từ my.sepay.vn), ADMIN_IDS (id telegram của bạn)

# 3. Chạy
python main.py
```

## Admin

Thêm `ADMIN_IDS` vào `.env` (ngăn cách dấu phẩy, vd `123,456`):

| Lệnh | Mô tả |
|------|-------|
| `/addsp <ma> <tên> <giá> [<số lượng>] [cat:<danh mục>]` | Thêm/sửa sản phẩm. Vd: `/addsp plus_1m "ChatGPT Plus 1 tháng" 220000 50 cat:ChatGPT` |
| `/addsp` + đính kèm file `.json` | Nhập hàng loạt từ file (cấu trúc như `products.json`) |
| `/delsp <ma>` | Xóa sản phẩm |
| `/listsp` | Xem danh sách + tồn kho |

> Tên có khoảng trắng phải nằm trong `"`.

## products.json

Dữ liệu sản phẩm, tự tạo khi admin `/addsp` đầu tiên. Cấu trúc (danh mục làm key ngoài):

```json
{
  "ChatGPT": {
    "chatgpt_plus_1m": { "name": "ChatGPT Plus 1 tháng", "price": 220000, "stock": 50 }
  }
}
```

Sửa file → khởi động lại bot để nạp. (Hoặc `/addsp` cho từng sản phẩm.)

## Lưu ý

- `.env` chứa token — đã trong `.gitignore`, **đừng đẩy lên git**.
- SePay API: chỉ tính giao dịch `transfer_type=in`, khớp đúng `transaction_content` + `amount_in`.
- QR hết hạn sau 10 phút (`PAY_WAIT_MIN` trong `main.py`).
