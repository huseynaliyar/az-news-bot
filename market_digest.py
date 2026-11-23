from __future__ import annotations

import html
import io
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("market-digest")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AzNewsBot/1.0; +https://github.com/)"
}

FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
FONT_REG_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = FONT_BOLD_CANDIDATES if bold else FONT_REG_CANDIDATES
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def get_baku_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=4)


def fetch_cbar_rates() -> tuple[dict[str, tuple[str, float]], str]:
    wanted = ["USD", "EUR", "TRY", "GBP", "RUB"]
    baku_now = get_baku_now()

    for days_back in range(4):
        dt = baku_now - timedelta(days=days_back)
        date_str = dt.strftime("%d.%m.%Y")
        url = f"https://www.cbar.az/currencies/{date_str}.xml"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200 or b"<ValCurs" not in resp.content:
                continue
            root = ET.fromstring(resp.content)
            rates: dict[str, tuple[str, float]] = {}
            for valute in root.iter("Valute"):
                code = valute.attrib.get("Code", "").upper()
                if code in wanted:
                    nominal = (valute.findtext("Nominal") or "1").strip()
                    val_text = (valute.findtext("Value") or "0").strip().replace(",", ".")
                    rates[code] = (nominal, float(val_text))
            if rates:
                return rates, date_str
        except Exception as exc:
            logger.warning("CBAR oxunmadı (%s): %s", date_str, exc)

    return {}, baku_now.strftime("%d.%m.%Y")


def fetch_yahoo_quote(symbol: str) -> tuple[float, float] | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        pct = ((price - prev) / prev * 100.0) if prev else 0.0
        return price, pct
    except Exception as exc:
        logger.warning("Yahoo quote oxunmadı (%s): %s", symbol, exc)
        return None


def fetch_crypto_quotes() -> dict[str, tuple[float, float]]:
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        out: dict[str, tuple[float, float]] = {}
        if "bitcoin" in data:
            out["BTC"] = (
                float(data["bitcoin"]["usd"]),
                float(data["bitcoin"].get("usd_24h_change", 0.0)),
            )
        if "ethereum" in data:
            out["ETH"] = (
                float(data["ethereum"]["usd"]),
                float(data["ethereum"].get("usd_24h_change", 0.0)),
            )
        return out
    except Exception as exc:
        logger.warning("CoinGecko oxunmadı: %s", exc)
        return {}


def render_market_card(
    baku_date: str,
    cbar_date: str,
    cbar_rates: dict[str, tuple[str, float]],
    markets: list[tuple[str, str, float]],
) -> bytes:
    W, H = 1080, 1080
    img = Image.new("RGB", (W, H), "#0B1120")
    draw = ImageDraw.Draw(img)

    for y in range(H):
        t = y / H
        r = int(11 + t * 8)
        g = int(17 + t * 12)
        b = int(32 + t * 22)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    draw.rectangle([(0, 0), (W, 10)], fill="#38BDF8")

    f_title = _load_font(44, bold=True)
    f_sub = _load_font(24, bold=False)
    f_sec = _load_font(26, bold=True)
    f_label = _load_font(28, bold=True)
    f_val = _load_font(30, bold=True)
    f_pct = _load_font(22, bold=True)
    f_foot = _load_font(20, bold=False)

    draw.text((64, 48), "GÜNÜN MALİYYƏ BÜLLETENİ", fill="#F8FAFC", font=f_title)
    draw.text((64, 108), f"Bakı | {baku_date}   •   AMB rəsmi məzənnə: {cbar_date}", fill="#94A3B8", font=f_sub)

    draw.rounded_rectangle([(56, 170), (W - 56, 515)], radius=24, fill="#131C31", outline="#1E293B", width=2)
    draw.text((88, 196), "RƏSMİ VALYUTA MƏZƏNNƏLƏRİ (AZN)", fill="#38BDF8", font=f_sec)

    code_names = {
        "USD": "ABŞ Dolları",
        "EUR": "Avro",
        "TRY": "Türkiyə Lirəsi",
        "GBP": "Funt Sterlinq",
        "RUB": "Rusiya Rublu",
    }
    ordered_codes = [c for c in ["USD", "EUR", "TRY", "GBP", "RUB"] if c in cbar_rates]

    y_pos = 248
    for idx, code in enumerate(ordered_codes):
        nominal, val = cbar_rates[code]
        if idx > 0:
            draw.line([(88, y_pos - 10), (W - 88, y_pos - 10)], fill="#1E293B", width=1)
        draw.text((88, y_pos), f"{nominal} {code}", fill="#F8FAFC", font=f_label)
        draw.text((260, y_pos + 3), code_names.get(code, ""), fill="#94A3B8", font=f_sub)
        val_str = f"{val:.4f} AZN"
        bbox = draw.textbbox((0, 0), val_str, font=f_val)
        draw.text((W - 88 - (bbox[2] - bbox[0]), y_pos - 2), val_str, fill="#E2E8F0", font=f_val)
        y_pos += 52

    if not ordered_codes:
        draw.text((88, 280), "Məzənnə məlumatı müvəqqəti əlçatan deyil", fill="#94A3B8", font=f_sub)

    draw.text((64, 548), "NEFT, QIZIL, BİRJA VƏ KRİPTO", fill="#38BDF8", font=f_sec)

    card_w, card_h, gap_x, gap_y = 468, 128, 32, 22
    start_x, start_y = 56, 594

    for idx, (label, price_str, pct) in enumerate(markets[:6]):
        col = idx % 2
        row = idx // 2
        x0 = start_x + col * (card_w + gap_x)
        y0 = start_y + row * (card_h + gap_y)
        x1, y1 = x0 + card_w, y0 + card_h

        draw.rounded_rectangle([(x0, y0), (x1, y1)], radius=20, fill="#131C31", outline="#1E293B", width=2)
        draw.text((x0 + 24, y0 + 20), label, fill="#94A3B8", font=f_sub)
        draw.text((x0 + 24, y0 + 62), price_str, fill="#F8FAFC", font=f_val)

        if pct > 0.05:
            badge_bg, badge_fg, arrow = "#064E3B", "#34D399", "▲"
        elif pct < -0.05:
            badge_bg, badge_fg, arrow = "#4C0519", "#FB7185", "▼"
        else:
            badge_bg, badge_fg, arrow = "#1E293B", "#CBD5E1", "•"

        pct_text = f"{arrow} {pct:+.2f}%"
        tb = draw.textbbox((0, 0), pct_text, font=f_pct)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        bx1 = x1 - 22
        bx0 = bx1 - tw - 24
        by0 = y0 + 62
        by1 = by0 + th + 16
        draw.rounded_rectangle([(bx0, by0), (bx1, by1)], radius=10, fill=badge_bg)
        draw.text((bx0 + 12, by0 + 6), pct_text, fill=badge_fg, font=f_pct)

    draw.text(
        (64, H - 46),
        "Mənbə: Azərbaycan Mərkəzi Bankı (cbar.az) • Yahoo Finance • CoinGecko",
        fill="#64748B",
        font=f_foot,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def generate_market_comment(summary_context: str) -> str:
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    prompt = (
        "Sən maliyyə və bazar analitikisən. Aşağıdakı bugünkü valyuta, neft, qızıl, "
        "birja və kripto göstəricilərinə baxıb azərbaycanlı oxucular üçün maksimum 2 qısa, "
        "canlı və anlaşılan cümləlik bazar şərhi yaz (yalnız mətni qaytar, başlıq yazma):\n\n"
        f"{summary_context}"
    )

    if gemini_key:
        for model in ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.8-flash"]:
            try:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
                    f":generateContent?key={gemini_key}"
                )
                resp = requests.post(
                    url,
                    json={"contents": [{"parts": [{"text": prompt}]}]},
                    timeout=30,
                )
                resp.raise_for_status()
                text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                if text:
                    return text
            except Exception as exc:
                logger.warning("Gemini bazar şərhi xətası (%s): %s", model, exc)

    if groq_key:
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {groq_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.warning("Groq bazar şərhi xətası: %s", exc)

    return ""


def pin_telegram_message(bot_token: str, chat_id: str, message_id: int) -> None:
    try:
        url = f"https://api.telegram.org/bot{bot_token}/pinChatMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "disable_notification": True,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Maliyyə postu kanalda Pin edildi (message_id=%s).", message_id)
    except Exception as exc:
        logger.warning("Pin edilmədi (botun Pin Messages icazəsini yoxlayın): %s", exc)


def run() -> None:
    load_dotenv()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN və TELEGRAM_CHAT_ID təyin edilməlidir.")

    baku_date = get_baku_now().strftime("%d.%m.%Y")
    cbar_rates, cbar_date = fetch_cbar_rates()

    markets: list[tuple[str, str, float]] = []
    for label, sym, prefix in [
        ("Brent Neft", "BZ=F", "$"),
        ("Qızıl (Gold)", "GC=F", "$"),
        ("S&P 500", "^GSPC", ""),
        ("BIST 100", "XU100.IS", ""),
    ]:
        quote = fetch_yahoo_quote(sym)
        if quote:
            price, pct = quote
            markets.append((label, f"{prefix}{price:,.2f}", pct))

    crypto = fetch_crypto_quotes()
    for sym, label in [("BTC", "Bitcoin (BTC)"), ("ETH", "Ethereum (ETH)")]:
        if sym in crypto:
            price, pct = crypto[sym]
            markets.append((label, f"${price:,.0f}", pct))

    card_png = render_market_card(
        baku_date=baku_date,
        cbar_date=cbar_date,
        cbar_rates=cbar_rates,
        markets=markets,
    )

    context_lines = [f"Tarix: {baku_date}"]
    for code, (nom, val) in cbar_rates.items():
        context_lines.append(f"{nom} {code} = {val:.4f} AZN")
    for label, price_str, pct in markets:
        context_lines.append(f"{label}: {price_str} ({pct:+.2f}%)")

    comment = generate_market_comment("\n".join(context_lines))

    caption_parts = [f"📊 <b>Günün Maliyyə & Birja Bülleteni | {baku_date}</b>"]
    if comment:
        caption_parts.append(f"💬 {html.escape(comment)}")
    caption_parts.append("🏷 #Maliyyə #Birja #Valyuta")
    caption = "\n\n".join(caption_parts)

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    resp = requests.post(
        url,
        data={
            "chat_id": chat_id,
            "caption": caption[:1024],
            "parse_mode": "HTML",
            "disable_notification": "false",
        },
        files={"photo": ("market_digest.png", card_png, "image/png")},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json()
    message_id = result.get("result", {}).get("message_id")
    logger.info("Dizaynlı Maliyyə Kartı Telegram-a göndərildi!")

    if message_id:
        pin_telegram_message(bot_token, chat_id, message_id)


if __name__ == "__main__":
    run()
