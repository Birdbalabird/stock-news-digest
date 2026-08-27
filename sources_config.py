"""
แหล่งข่าว/ฟีดสำหรับระบบสรุปข่าวหุ้นประจำวัน

แก้ dict FEEDS ด้านล่างเพื่อเพิ่ม/ลบ/เปลี่ยน source หรือ keyword ได้เลย
โดยไม่ต้องแตะ news_digest.py — โครงสร้างคือ

    "ชื่อ category": [
        "url feed 1",
        "url feed 2",
        ...
    ]

หมายเหตุสำคัญเรื่อง SEC EDGAR:
SEC กำหนดว่าทุก request ต้องส่ง User-Agent ที่ระบุตัวตน (ชื่อ/บริษัท + อีเมลติดต่อ)
ไม่งั้นอาจโดน block (403) — แก้ SEC_USER_AGENT ด้านล่างเป็นของคุณเอง
ดูรายละเอียด: https://www.sec.gov/os/webmaster-faq#developers
"""

# ตัวอย่าง: "Chaipon K contact@example.com"  <-- แก้เป็นชื่อ/อีเมลจริงของคุณ
SEC_USER_AGENT = "c.kiattipon@gmail.com"

# ดึงข่าวย้อนหลังกี่ชั่วโมง (ตามโจทย์ = 24 ชม.)
LOOKBACK_HOURS = 24

FEEDS = {
    "Insider & Filings": [
        # 8-K = material events, Form 4 = insider buy/sell
        # NOTE: URL แบบ action=getcompany "เปล่า ๆ" (ไม่มี company หรือ CIK) จะไม่คืนผลลัพธ์ที่เป็นประโยชน์
        # แนะนำให้ระบุบริษัทที่ติดตาม เช่น company=nvidia (ดูวิธีแก้ใน README หัวข้อ "เปลี่ยนหุ้นที่ติดตาม")
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=8-K&dateb=&owner=include&count=40&output=atom",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=4&dateb=&owner=include&count=40&output=atom",
    ],
    "Earnings & Guidance": [
        "https://news.google.com/rss/search?q=%22Nvidia+earnings%22+OR+%22Meta+earnings%22+OR+%22Amazon+earnings%22&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=%22Google+earnings%22+OR+%22Apple+earnings%22+OR+%22Visa+earnings%22&hl=en-US&gl=US&ceid=US:en",
    ],
    "M&A / Deal Rumors": [
        "https://news.google.com/rss/search?q=acquisition+OR+merger+Google+OR+Apple+OR+Meta+OR+Amazon&hl=en-US&gl=US&ceid=US:en",
    ],
    "FDA / Pharma Catalyst": [
        "https://news.google.com/rss/search?q=Eli+Lilly+FDA+OR+trial+OR+approval&hl=en-US&gl=US&ceid=US:en",
    ],
    "Semiconductor / AI Infra Trend": [
        "https://news.google.com/rss/search?q=ASML+OR+EUV+semiconductor&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=Nvidia+OR+Meta+OR+Google+AI+capex&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=Palantir+PLTR&hl=en-US&gl=US&ceid=US:en",
    ],
}
