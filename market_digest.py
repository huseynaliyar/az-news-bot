from __future__ import annotations

import html
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("market-digest")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AzNewsBot/1.0; +https://github.com/)"
}


def get_baku_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=4)


def fetch_cbar_rates() -> tuple[dict[str, tuple[str, float]], str]:
    wanted = ["USD", "EUR", "TRY", "RUB", "GBP"]
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


def _fmt_pct(pct: float) -> str:
    arrow = "🟢 ▲" if pct > 0.05 else ("🔴 ▼" if pct < -0.05 else "⚪️")
    return f"{arrow} {pct:+.2f}%"


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


def build_digest_message() -> str:
    baku_date = get_baku_now().strftime("%d.%m.%Y")
    cbar_rates, cbar_date = fetch_cbar_rates()

    market_symbols = [
        ("🛢 Brent neft", "BZ=F", "$"),
        ("🥇 Qızıl", "GC=F", "$"),
        ("🇺🇸 S&P 500", "^GSPC", ""),
        ("🇹🇷 BIST 100", "XU100.IS", ""),
    ]
    market_lines: list[str] = []
    for label, sym, prefix in market_symbols:
        quote = fetch_yahoo_quote(sym)
        if quote:
            price, pct = quote
            market_lines.append(f"• {label}: <b>{prefix}{price:,.2f}</b> ({_fmt_pct(pct)})")

    crypto = fetch_crypto_quotes()
    for sym, label in [("BTC", "₿ Bitcoin"), ("ETH", "Ξ Ethereum")]:
        if sym in crypto:
            price, pct = crypto[sym]
            market_lines.append(f"• {label}: <b>${price:,.0f}</b> ({_fmt_pct(pct)})")

    flags = {"USD": "🇺🇸", "EUR": "🇪🇺", "TRY": "🇹🇷", "GBP": "🇬🇧", "RUB": "🇷🇺"}
    rate_lines: list[str] = []
    for code in ["USD", "EUR", "TRY", "GBP", "RUB"]:
        if code in cbar_rates:
            nominal, val = cbar_rates[code]
            rate_lines.append(f"{flags[code]} {nominal} {code} = <b>{val:.4f} AZN</b>")

    parts = [f"📊 <b>Günün Maliyyə & Birja Bülleteni | {baku_date}</b>"]

    if rate_lines:
        parts.append(f"💱 <b>Rəsmi məzənnələr (AMB — {cbar_date}):</b>\n" + "\n".join(rate_lines))

    if market_lines:
        parts.append("📈 <b>Neft, Qızıl, Birja & Kripto:</b>\n" + "\n".join(market_lines))

    plain_context = "\n".join(parts)
    comment = generate_market_comment(plain_context)
    if comment:
        parts.append(f"💬 <b>Qısa şərh:</b> {html.escape(comment)}")

    parts.append("🏷 #Maliyyə #Birja #Valyuta")
    return "\n\n".join(parts)


def run() -> None:
    load_dotenv()
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN və TELEGRAM_CHAT_ID təyin edilməlidir.")

    message = build_digest_message()
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    resp.raise_for_status()
    logger.info("Günün Maliyyə & Birja Bülleteni Telegram-a göndərildi!")


if __name__ == "__main__":
    run()
