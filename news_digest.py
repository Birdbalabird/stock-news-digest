"""
ระบบสรุปข่าวหุ้น/เทคอัตโนมัติรายวัน

ขั้นตอน:
1. ดึง RSS/Atom feed จาก sources_config.py (แยกตาม category)
2. กรองเฉพาะรายการที่โพสต์ใน LOOKBACK_HOURS ชั่วโมงล่าสุด
3. ส่งข้อมูลทั้งหมดให้ Gemini API สรุปเป็นภาษาไทย จัดกลุ่มตาม category
4. ส่งอีเมลสรุปผ่าน Gmail SMTP

รันด้วย: python news_digest.py
ต้องตั้ง environment variables ก่อน (ดู README.md):
    GEMINI_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL
"""

import calendar
import logging
import os
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser
import requests

from sources_config import FEEDS, LOOKBACK_HOURS, SEC_USER_AGENT

# โหลดตัวแปรจากไฟล์ .env ถ้ามี (สำหรับทดสอบรันในเครื่อง) — ไม่บังคับต้องมี python-dotenv
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    # กัน log ภาษาไทยแสดงเพี้ยนบน Windows console ที่ไม่ใช้ UTF-8 เป็นค่า default
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("news_digest")

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
REQUEST_TIMEOUT = 15  # วินาที

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]


def thai_date_str(dt: datetime) -> str:
    return f"{dt.day} {THAI_MONTHS[dt.month]} {dt.year + 543}"


def fetch_feed(url: str) -> feedparser.FeedParserDict:
    """ดึงและ parse feed หนึ่งอัน คืนค่า None ถ้าดึงไม่สำเร็จ"""
    headers = {}
    if "sec.gov" in url:
        # SEC EDGAR บังคับให้ระบุ User-Agent ที่มีชื่อ/อีเมลติดต่อ ไม่งั้นจะโดน 403
        headers["User-Agent"] = SEC_USER_AGENT

    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
        if parsed.bozo and not parsed.entries:
            log.warning("Feed ไม่สามารถ parse ได้ (bozo): %s", url)
            return None
        return parsed
    except Exception as exc:  # noqa: BLE001 - ต้องไม่ให้ feed เดียวทำ flow พังทั้งหมด
        log.warning("ดึง feed ไม่สำเร็จ ข้ามไป: %s (%s)", url, exc)
        return None


def entry_datetime_utc(entry) -> datetime | None:
    """หา timestamp ของ entry (published หรือ updated) เป็น UTC datetime"""
    struct_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if not struct_time:
        return None
    # feedparser normalize เวลาที่ parse ได้เป็น UTC struct_time เสมอ
    epoch = calendar.timegm(struct_time)
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def collect_recent_entries() -> dict[str, list[dict]]:
    """ดึงทุก feed ตาม category แล้วกรองเฉพาะข่าวใน LOOKBACK_HOURS ชม.ล่าสุด"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    results: dict[str, list[dict]] = {cat: [] for cat in FEEDS}

    for category, urls in FEEDS.items():
        for url in urls:
            log.info("กำลังดึง [%s]: %s", category, url)
            parsed = fetch_feed(url)
            if parsed is None:
                continue

            kept = 0
            for entry in parsed.entries:
                dt = entry_datetime_utc(entry)
                if dt is None or dt < cutoff:
                    continue
                results[category].append(
                    {
                        "title": entry.get("title", "(ไม่มีหัวข้อ)"),
                        "link": entry.get("link", ""),
                        "published": dt,
                    }
                )
                kept += 1
            log.info("  -> เก็บได้ %d รายการ (ใน %d ชม.ล่าสุด)", kept, LOOKBACK_HOURS)

    return results


def build_news_text(grouped: dict[str, list[dict]]) -> str:
    """แปลงข่าวที่กรองแล้วเป็นข้อความดิบ สำหรับส่งเข้า Gemini prompt"""
    lines = []
    for category, items in grouped.items():
        if not items:
            continue
        lines.append(f"## {category}")
        for item in items:
            ts = item["published"].strftime("%Y-%m-%d %H:%M UTC")
            lines.append(f"- [{ts}] {item['title']} | link: {item['link']}")
        lines.append("")
    return "\n".join(lines)


def summarize_with_gemini(news_text: str) -> str:
    """เรียก Gemini API ให้สรุปข่าวเป็น HTML ภาษาไทย"""
    import google.generativeai as genai

    api_key = os.environ["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    prompt = f"""คุณเป็นผู้ช่วยติดตามข่าวการลงทุน สรุปข้อมูลต่อไปนี้เป็นภาษาไทย แบบ bullet สั้นๆ อ่านเร็ว
จัดกลุ่มตาม category (Insider & Filings, Earnings & Guidance, M&A, FDA/Pharma, Semiconductor/AI Trend)

สำหรับแต่ละรายการ ให้ระบุ:
- เกิดอะไรขึ้น (สรุปสั้น)
- ทำไมอาจสำคัญต่อการลงทุน (ถ้าไม่ชัดเจนให้บอกตรงๆ ว่า 'ยังไม่ชัดเจนว่ากระทบอย่างไร' แทนที่จะเดา)
- ใส่ลิงก์กำกับท้ายแต่ละข่าว

หมายเหตุ: อย่าตีความเกินจากข้อมูลที่มี อย่าฟันธงว่าราคาหุ้นจะขึ้นหรือลง แค่รายงานข้อเท็จจริงและความสำคัญเชิงข่าว

รูปแบบผลลัพธ์: ตอบเป็น HTML fragment เท่านั้น (ห้ามมี <html>, <head>, <body>)
ใช้ <h2> สำหรับชื่อ category, <ul>/<li> สำหรับแต่ละข่าว, <a href="...">ลิงก์</a> สำหรับลิงก์
ถ้า category ไหนไม่มีข่าว ให้ข้าม category นั้นไปเลย

ข้อมูล:
{news_text}
"""

    response = model.generate_content(prompt)
    return response.text


def build_email_html(body_html: str, date_str: str) -> str:
    return f"""\
<html>
  <body style="font-family: Arial, sans-serif; max-width: 640px; margin: 0 auto; color: #222;">
    <h1 style="font-size: 20px;">📈 สรุปข่าวหุ้นประจำวัน {date_str}</h1>
    <div style="font-size: 15px; line-height: 1.6;">
      {body_html}
    </div>
    <hr style="margin-top: 24px; border: none; border-top: 1px solid #ddd;">
    <p style="font-size: 12px; color: #888;">
      ส่งอัตโนมัติโดย news_digest.py ผ่าน GitHub Actions — ข้อมูลนี้ไม่ใช่คำแนะนำการลงทุน
    </p>
  </body>
</html>
"""


def send_email(subject: str, html_body: str) -> None:
    gmail_address = os.environ["GMAIL_ADDRESS"]
    gmail_app_password = os.environ["GMAIL_APP_PASSWORD"]
    recipient_email = os.environ["RECIPIENT_EMAIL"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = recipient_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(gmail_address, gmail_app_password)
        server.sendmail(gmail_address, [recipient_email], msg.as_string())

    log.info("ส่งอีเมลสำเร็จไปยัง %s", recipient_email)


def main() -> None:
    log.info("=== เริ่มรัน news_digest ===")
    now = datetime.now(timezone.utc)
    date_str = thai_date_str(now)
    subject = f"สรุปข่าวหุ้นประจำวัน {date_str}"

    grouped = collect_recent_entries()
    total = sum(len(v) for v in grouped.values())
    log.info("รวมข่าวที่เข้าเงื่อนไขทั้งหมด: %d รายการ", total)

    if total == 0:
        log.info("ไม่มีข่าวใหม่ใน %d ชม. — ส่งอีเมลแจ้งว่าไม่มีข่าวสำคัญ", LOOKBACK_HOURS)
        html_body = "<p>วันนี้ไม่มีข่าวสำคัญจากแหล่งข้อมูลที่ติดตามใน 24 ชั่วโมงที่ผ่านมา</p>"
        send_email(subject, build_email_html(html_body, date_str))
        log.info("=== จบการทำงาน ===")
        return

    news_text = build_news_text(grouped)

    try:
        summary_html = summarize_with_gemini(news_text)
    except Exception as exc:  # noqa: BLE001 - ถ้า Gemini ล่ม ให้ส่งข่าวดิบแทน ดีกว่าไม่ส่งอะไรเลย
        log.error("เรียก Gemini API ไม่สำเร็จ ใช้รายการข่าวดิบแทน: %s", exc)
        fallback_lines = ["<p><i>(สรุปด้วย AI ไม่สำเร็จ แสดงหัวข้อข่าวดิบแทน)</i></p>"]
        for category, items in grouped.items():
            if not items:
                continue
            fallback_lines.append(f"<h2>{category}</h2><ul>")
            for item in items:
                fallback_lines.append(
                    f'<li><a href="{item["link"]}">{item["title"]}</a></li>'
                )
            fallback_lines.append("</ul>")
        summary_html = "\n".join(fallback_lines)

    send_email(subject, build_email_html(summary_html, date_str))
    log.info("=== จบการทำงาน ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("news_digest ล้มเหลวแบบไม่คาดคิด")
        sys.exit(1)
