"""
ระบบสรุปข่าวหุ้น/เทคอัตโนมัติรายวัน

ขั้นตอน:
1. ดึง RSS/Atom feed จาก sources_config.py (แยกตาม category)
2. กรองเฉพาะรายการที่โพสต์ใน LOOKBACK_HOURS ชั่วโมงล่าสุด
3. ส่งข้อมูลทั้งหมดให้ Gemini API สรุปเป็น JSON (ภาษาไทย, แยกตาม category, ดึงตัวเลขการเงินที่ระบุตรงๆ ในข่าว)
4. Python แปลง JSON เป็น HTML ที่จัดหน้าเป็นสัดส่วนชัดเจน (การ์ดต่อ category + กล่องสรุปตัวเลขการเงิน)
5. ส่งอีเมลสรุปผ่าน Gmail SMTP

รันด้วย: python news_digest.py
ต้องตั้ง environment variables ก่อน (ดู README.md):
    GEMINI_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL
"""

import calendar
import html
import json
import logging
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import feedparser
import requests

from sources_config import CATEGORY_META, FEEDS, LOOKBACK_HOURS, SEC_USER_AGENT

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
DEFAULT_CATEGORY_META = {"emoji": "📰", "color": "#374151", "label_th": ""}

THAI_MONTHS = [
    "", "มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม",
]


def thai_date_str(dt: datetime) -> str:
    return f"{dt.day} {THAI_MONTHS[dt.month]} {dt.year + 543}"


def strip_html(raw: str) -> str:
    """ตัด HTML tag ออกจาก summary/description ของ feed แล้ว unescape entity"""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


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
                        "summary": strip_html(entry.get("summary", ""))[:400],
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
            lines.append(f"- [{ts}] {item['title']}")
            if item["summary"]:
                lines.append(f"  รายละเอียด: {item['summary']}")
            lines.append(f"  link: {item['link']}")
        lines.append("")
    return "\n".join(lines)


def summarize_with_gemini(news_text: str) -> dict:
    """เรียก Gemini API ให้วิเคราะห์ข่าว คืนค่าเป็น dict (parse จาก JSON ที่ Gemini ตอบกลับ)"""
    import google.generativeai as genai

    api_key = os.environ["GEMINI_API_KEY"]
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    category_list = "\n".join(f"- {c}" for c in FEEDS)

    prompt = f"""คุณเป็นผู้ช่วยติดตามข่าวการลงทุน วิเคราะห์ข้อมูลข่าวด้านล่าง แล้วตอบกลับเป็น JSON เท่านั้น
(ห้ามมี markdown code fence เช่น ```json ห้ามมีข้อความอื่นใดนอก JSON)

โครงสร้าง JSON ที่ต้องการเป๊ะๆ:
{{
  "categories": {{
    "<ชื่อ category ต้องตรงกับที่ให้มาด้านล่างเป๊ะๆ ตัวอักษรต่อตัวอักษร>": [
      {{
        "headline": "สรุปสั้นๆ ว่าเกิดอะไรขึ้น (ภาษาไทย 1 ประโยค)",
        "why_matters": "ทำไมอาจสำคัญต่อการลงทุน ถ้าไม่ชัดเจนให้เขียนตรงๆ ว่า 'ยังไม่ชัดเจนว่ากระทบอย่างไร' ห้ามเดา",
        "numbers": ["ตัวเลขการเงินที่ระบุไว้ตรงๆ ในข่าวเท่านั้น เช่น 'รายได้ 35.1 พันล้านดอลลาร์ (+94% YoY)', 'EPS 1.05 ดอลลาร์ เทียบคาดการณ์ 0.98 ดอลลาร์', 'มูลค่าดีล 2 หมื่นล้านดอลลาร์'"],
        "link": "ลิงก์ข่าวเดิม คัดลอกมาตรงๆ จากข้อมูลด้านล่าง"
      }}
    ]
  }}
}}

กติกาสำคัญ:
1. category ต้องเป็นหนึ่งใน:
{category_list}
   ใช้เฉพาะ category ที่มีข่าวจริงเท่านั้น ข้ามอันที่ไม่มีข่าวไปเลย ห้ามสร้าง category ใหม่
2. "numbers" ใส่เฉพาะตัวเลขการเงิน/ผลประกอบการที่ปรากฏตรงๆ ในข้อมูลข่าวเท่านั้น
   (รายได้, กำไร/ขาดทุนสุทธิ, EPS, % เติบโต YoY/QoQ, มูลค่าดีล, guidance, ผลทดลองยา %, ราคาเป้าหมาย)
   ถ้าข่าวไม่มีตัวเลขระบุไว้ ให้ใส่ list ว่าง [] ห้ามประมาณ/เดา/คำนวณตัวเลขขึ้นเองเด็ดขาด
3. อย่าตีความเกินจากข้อมูลที่มี อย่าฟันธงว่าราคาหุ้นจะขึ้นหรือลง แค่รายงานข้อเท็จจริงและความสำคัญเชิงข่าว
4. link ต้อง copy มาจากข้อมูลด้านล่างตรงๆ ห้ามแต่งขึ้นเอง

ข้อมูลข่าว:
{news_text}
"""

    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(response_mime_type="application/json"),
    )
    return json.loads(response.text)


def esc(text: str) -> str:
    return html.escape(text or "", quote=True)


def safe_link(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return esc(url)
    return "#"


def render_numbers_chips(numbers: list[str]) -> str:
    if not numbers:
        return ""
    chips = "".join(
        f'<span style="display:inline-block;background:#fffbeb;border:1px solid #fde68a;'
        f'color:#92400e;border-radius:6px;padding:2px 8px;margin:2px 6px 2px 0;font-size:12px;">'
        f"💰 {esc(n)}</span>"
        for n in numbers
        if n
    )
    return f'<div style="margin-top:4px;">{chips}</div>' if chips else ""


def render_digest_html(data: dict) -> str:
    """แปลง dict ที่ได้จาก Gemini (JSON) เป็น HTML: กล่องสรุปตัวเลขการเงิน + การ์ดตาม category"""
    categories = data.get("categories") or {}
    known_order = list(FEEDS)
    extra_order = [c for c in categories if c not in known_order]
    ordered_categories = known_order + extra_order

    highlight_rows: list[str] = []
    section_html: list[str] = []

    for category in ordered_categories:
        items = categories.get(category) or []
        if not items:
            continue
        meta = {**DEFAULT_CATEGORY_META, **CATEGORY_META.get(category, {"label_th": category})}
        label = meta["label_th"] or category

        item_html = []
        for item in items:
            if not isinstance(item, dict):
                continue
            headline = esc(item.get("headline", ""))
            why = esc(item.get("why_matters", ""))
            link = safe_link(item.get("link", ""))
            numbers = [n for n in (item.get("numbers") or []) if isinstance(n, str) and n.strip()]

            if numbers:
                highlight_rows.append(
                    f'<li style="margin-bottom:6px;">{meta["emoji"]} <b>{headline}</b> — '
                    + " | ".join(esc(n) for n in numbers)
                    + "</li>"
                )

            item_html.append(
                f'<li style="margin-bottom:14px;">'
                f'<div style="font-weight:600;">{headline}</div>'
                f'<div style="color:#4b5563;font-size:13px;margin-top:2px;">{why}</div>'
                f"{render_numbers_chips(numbers)}"
                f'<div style="margin-top:4px;"><a href="{link}" style="font-size:12px;color:{meta["color"]};">อ่านข่าวเต็ม →</a></div>'
                f"</li>"
            )

        if not item_html:
            continue

        section_html.append(
            f'<div style="border-left:4px solid {meta["color"]};background:#f9fafb;'
            f'border-radius:8px;padding:12px 16px;margin-bottom:16px;">'
            f'<h2 style="font-size:16px;margin:0 0 8px 0;color:{meta["color"]};">{meta["emoji"]} {esc(label)}</h2>'
            f'<ul style="margin:0;padding-left:18px;">{"".join(item_html)}</ul>'
            f"</div>"
        )

    if highlight_rows:
        highlight_box = (
            '<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;'
            'padding:12px 16px;margin-bottom:20px;">'
            '<h2 style="font-size:16px;margin:0 0 8px 0;color:#92400e;">🧮 ตัวเลขการเงินสำคัญวันนี้</h2>'
            f'<ul style="margin:0;padding-left:18px;font-size:13px;color:#78350f;">{"".join(highlight_rows[:10])}</ul>'
            "</div>"
        )
    else:
        highlight_box = (
            '<div style="background:#f3f4f6;border-radius:8px;padding:12px 16px;'
            'margin-bottom:20px;font-size:13px;color:#6b7280;">'
            "🧮 วันนี้ไม่มีตัวเลขการเงินที่ระบุชัดเจนในข่าวที่ติดตาม"
            "</div>"
        )

    if not section_html:
        return highlight_box + "<p>วันนี้ไม่มีข่าวสำคัญจากแหล่งข้อมูลที่ติดตามใน 24 ชั่วโมงที่ผ่านมา</p>"

    return highlight_box + "".join(section_html)


def render_fallback_html(grouped: dict[str, list[dict]]) -> str:
    """ใช้ตอน Gemini เรียกไม่สำเร็จ หรือ parse JSON ไม่ได้ — แสดงหัวข้อข่าวดิบแต่จัดหน้าแบบเดียวกับปกติ"""
    lines = ['<p><i>(สรุปด้วย AI ไม่สำเร็จ แสดงหัวข้อข่าวดิบแทน — ไม่มีตัวเลขการเงินสรุปให้ในกรณีนี้)</i></p>']
    for category, items in grouped.items():
        if not items:
            continue
        meta = {**DEFAULT_CATEGORY_META, **CATEGORY_META.get(category, {"label_th": category})}
        label = meta["label_th"] or category
        lines.append(
            f'<div style="border-left:4px solid {meta["color"]};background:#f9fafb;'
            f'border-radius:8px;padding:12px 16px;margin-bottom:16px;">'
            f'<h2 style="font-size:16px;margin:0 0 8px 0;color:{meta["color"]};">{meta["emoji"]} {esc(label)}</h2><ul>'
        )
        for item in items:
            link = safe_link(item["link"])
            lines.append(f'<li><a href="{link}">{esc(item["title"])}</a></li>')
        lines.append("</ul></div>")
    return "\n".join(lines)


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
        data = summarize_with_gemini(news_text)
        summary_html = render_digest_html(data)
    except Exception as exc:  # noqa: BLE001 - ถ้า Gemini/JSON ล่ม ให้ส่งข่าวดิบแทน ดีกว่าไม่ส่งอะไรเลย
        log.error("เรียก Gemini API หรือ parse ผลลัพธ์ไม่สำเร็จ ใช้รายการข่าวดิบแทน: %s", exc)
        summary_html = render_fallback_html(grouped)

    send_email(subject, build_email_html(summary_html, date_str))
    log.info("=== จบการทำงาน ===")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("news_digest ล้มเหลวแบบไม่คาดคิด")
        sys.exit(1)
