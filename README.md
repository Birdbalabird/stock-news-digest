# สรุปข่าวหุ้น/เทคอัตโนมัติรายวัน

ระบบดึงข่าว/filing ที่อาจเป็น "catalyst" ต่อราคาหุ้น กรองเฉพาะ 24 ชม.ล่าสุด สรุปด้วย Gemini
เป็นภาษาไทย แล้วส่งอีเมลให้อัตโนมัติทุกเช้า 08:00 น. (เวลาไทย) ผ่าน GitHub Actions (ฟรี)

## ไฟล์ในโปรเจกต์

- `news_digest.py` — สคริปต์หลัก: ดึงข่าว → กรอง 24 ชม. → สรุปด้วย Gemini → ส่งอีเมล
- `sources_config.py` — รายการ RSS/Atom feed แยกตาม category แก้ตรงนี้อย่างเดียวถ้าจะเปลี่ยนหุ้น/แหล่งข่าว
- `.github/workflows/daily-news.yml` — ตั้ง cron ให้รันทุกวัน 01:00 UTC (=08:00 ไทย)
- `requirements.txt` — Python packages ที่ต้องใช้
- `.env.example` — ตัวอย่างไฟล์ env สำหรับทดสอบรันในเครื่อง

---

## Step 1 — สมัคร Gemini API Key (ฟรี)

1. ไปที่ https://aistudio.google.com/app/apikey
2. ล็อกอินด้วย Google account
3. กด **Create API key** → เลือกโปรเจกต์ (หรือสร้างใหม่) → คัดลอกคีย์ที่ได้
4. เก็บค่านี้ไว้ก่อน จะเอาไปใส่ใน GitHub Secrets ชื่อ `GEMINI_API_KEY`

Free tier ของ `gemini-3.6-flash` เพียงพอสำหรับงานนี้ (เรียกวันละครั้ง) Google เปลี่ยนรุ่นโมเดล/เลิกรองรับรุ่นเก่าอยู่เรื่อย ๆ
(ตอนเขียนสคริปต์นี้ `gemini-2.0-flash` ที่เคยใช้ได้ถูกปลดระวางไปแล้ว และ Google แนะนำให้ย้ายมาที่ `gemini-3.6-flash`)
ถ้าในอนาคต Google เปลี่ยนรุ่นอีก ให้ตั้ง GitHub Secret เพิ่ม (ไม่บังคับ) ชื่อ `GEMINI_MODEL` เป็นชื่อรุ่นใหม่
(ถ้าไม่ตั้ง จะใช้ `gemini-3.6-flash` เป็นค่า default)

## Step 2 — สร้าง Gmail App Password

Gmail ไม่อนุญาตให้ล็อกอินผ่าน SMTP ด้วยรหัสผ่านจริงอีกต่อไป ต้องสร้าง "App Password" แยก:

1. เปิด 2-Step Verification ก่อน (ถ้ายังไม่เปิด): https://myaccount.google.com/security → **2-Step Verification** → เปิดใช้งาน
2. ไปที่ https://myaccount.google.com/apppasswords
3. ตั้งชื่อ app เช่น `news-digest` → กด **Create**
4. Google จะให้รหัส 16 ตัวอักษร (เช่น `abcd efgh ijkl mnop`) — คัดลอกไว้ (ไม่ต้องมีเว้นวรรคตอนใช้จริงก็ได้)
5. ค่านี้จะใช้เป็น GitHub Secret ชื่อ `GMAIL_APP_PASSWORD`

## Step 3 — SEC EDGAR User-Agent

SEC บังคับให้ทุก request ต้องมี HTTP header `User-Agent` ที่ระบุชื่อ/บริษัท + อีเมลติดต่อ จริง ๆ
ไม่งั้นจะโดนบล็อก (403) — อ้างอิง: https://www.sec.gov/os/webmaster-faq#developers

เปิดไฟล์ `sources_config.py` แล้วแก้บรรทัดนี้เป็นของคุณเอง:

```python
SEC_USER_AGENT = "YourName YourEmail@example.com"
```

เช่น `SEC_USER_AGENT = "Chaipon K c.kiattipon@gmail.com"` — ไม่ใช่ secret จึง commit ลง repo ได้ตามปกติ

**ข้อควรรู้เพิ่ม**: URL ที่ใช้ `action=getcompany` แบบไม่ระบุบริษัท (ตามที่ตั้งต้นไว้ใน `sources_config.py`)
จะไม่คืนผลลัพธ์ที่มีประโยชน์ เพราะ EDGAR ต้องการ CIK หรือชื่อบริษัทกำกับ สคริปต์จะ handle กรณีนี้แบบไม่ error
(แค่ได้ 0 รายการ) แต่ถ้าอยากให้ฟีเจอร์ "Insider & Filings" ทำงานได้จริง ให้ดูวิธีแก้ใน Step 6 ด้านล่าง

## Step 4 — ทดสอบรันในเครื่องก่อน deploy

```bash
# 1) สร้าง virtual environment (แนะนำ)
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# 2) ติดตั้ง dependencies
pip install -r requirements.txt

# 3) สร้างไฟล์ .env จากตัวอย่าง แล้วกรอกค่าจริง
copy .env.example .env        # Windows
# cp .env.example .env        # macOS/Linux
```

แก้ `.env` ให้มีค่าจริงครบ 4 ตัว:

```
GEMINI_API_KEY=...
GMAIL_ADDRESS=youraddress@gmail.com
GMAIL_APP_PASSWORD=...
RECIPIENT_EMAIL=youraddress@gmail.com
```

แล้วรัน:

```bash
python news_digest.py
```

ดู log ที่ print ออกมา — ถ้าทุกอย่างถูกต้องจะเห็นข่าวที่ดึงได้ต่อ feed แล้วอีเมลจะเข้ากล่องจดหมายของ `RECIPIENT_EMAIL`
ภายในไม่กี่วินาที **`.env` จะไม่ถูก commit ขึ้น GitHub** (มี `.gitignore` กันไว้แล้ว)

## Step 5 — Push ขึ้น GitHub

```bash
git init
git add .
git commit -m "Add daily stock news digest"
git branch -M main
git remote add origin https://github.com/<your-username>/<repo-name>.git
git push -u origin main
```

> repo จะเป็น public หรือ private ก็ได้ — GitHub Actions cron ใช้ได้ทั้งคู่
> (private repo มีโควตานาทีฟรีจำกัดต่อเดือน แต่ job นี้ใช้เวลาไม่กี่นาที/วัน ไม่น่ากระทบ)

## Step 6 — ตั้งค่า GitHub Secrets

ไปที่ repo บน GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
เพิ่มทีละตัว 4 ตัวนี้:

| Secret name | ค่า |
|---|---|
| `GEMINI_API_KEY` | คีย์จาก Step 1 |
| `GMAIL_ADDRESS` | อีเมล Gmail ที่ใช้ส่ง |
| `GMAIL_APP_PASSWORD` | App password จาก Step 2 |
| `RECIPIENT_EMAIL` | อีเมลปลายทางที่จะรับสรุป (จะใส่อีเมลเดียวกับผู้ส่งก็ได้) |

**ไม่ต้องตั้ง secret สำหรับ SEC User-Agent** — ค่านั้นแก้ตรงในไฟล์ `sources_config.py` แล้ว (Step 3)

## Step 7 — ทดสอบรันบน GitHub Actions

1. ไปแท็บ **Actions** ของ repo
2. เลือก workflow **Daily Stock News Digest**
3. กด **Run workflow** (ปุ่มขวาบน) เพื่อรันทันทีโดยไม่ต้องรอ cron
4. เปิดดู log ของ run นั้น — ทุกขั้นตอน (ดึง feed ไหนได้/ไม่ได้, จำนวนข่าว, ส่งอีเมลสำเร็จ) จะถูก log ไว้ให้ดูย้อนหลังได้เสมอ
5. ถ้าอีเมลมาถึง แปลว่า setup เสร็จสมบูรณ์ — จากนี้จะรันอัตโนมัติทุกวัน 08:00 น. ตามเวลาไทย

---

## วิธีแก้ ticker/keyword ที่ติดตาม (สำหรับอนาคต)

เปิดไฟล์ `sources_config.py` แก้ที่ dict `FEEDS`:

**เพิ่ม/ลด keyword ข่าวทั่วไป (Google News RSS)** — แก้ query ใน URL ได้ตรง ๆ เช่น เปลี่ยน
```
q=Eli+Lilly+FDA+OR+trial+OR+approval
```
เป็น
```
q=Pfizer+FDA+OR+trial+OR+approval
```
(เว้นวรรคแทนด้วย `+`, ใช้ `OR` ตัวใหญ่คั่นเงื่อนไข) ทดสอบ URL ได้ตรง ๆ ในเบราว์เซอร์ก่อนใส่ลงไฟล์

**เพิ่ม/แก้ SEC EDGAR feed ให้ติดตามบริษัทเฉพาะเจาะจง** (แนะนำ ถ้าอยากให้ Insider & Filings ใช้งานได้จริง)
ใช้ query param `company=` แทนการปล่อยว่าง เช่น สำหรับ NVIDIA:
```
https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company=nvidia&type=8-K&dateb=&owner=include&count=40&output=atom
```
เปลี่ยน `company=nvidia` เป็นชื่อบริษัทอื่น (เช่น `apple`, `meta+platforms`, `eli+lilly`) และ `type=8-K` เป็น `type=4`
สำหรับ insider trading ได้เช่นกัน — ทำแบบนี้ 1 feed ต่อ 1 บริษัทที่อยากติดตาม แล้วเพิ่มเข้า list ใน category
`"Insider & Filings"`

**เพิ่ม category ใหม่ทั้งหมด** — เพิ่ม key ใหม่ใน dict `FEEDS` พร้อม list ของ URL แล้วสคริปต์จะดึง/ส่งเข้า Gemini
ให้จัดกลุ่มอัตโนมัติทันที ไม่ต้องแก้ `news_digest.py`

**เปลี่ยนช่วงเวลาที่กรองข่าว** — แก้ `LOOKBACK_HOURS = 24` เป็นจำนวนชั่วโมงอื่นได้

**เปลี่ยนเวลารันแต่ละวัน** — แก้ cron ใน `.github/workflows/daily-news.yml` (เวลาที่ตั้งเป็น UTC เสมอ
เช่น 08:00 ไทย = 01:00 UTC, สูตรคือ เวลาไทย − 7 ชั่วโมง)

---

## หมายเหตุ

- ถ้าไม่มีข่าวใหม่เลยในรอบ 24 ชม. ระบบจะส่งอีเมลแจ้งว่า "วันนี้ไม่มีข่าวสำคัญ" แทนที่จะ error หรือไม่ส่งอะไรเลย
- ถ้า feed ใด feed หนึ่งดึงไม่ได้ (network error, 403 ฯลฯ) ระบบจะ log คำเตือนแล้วข้ามไปแหล่งอื่นต่อ ไม่ทำให้ทั้ง flow ล้มเหลว
- ถ้า Gemini API เรียกไม่สำเร็จ ระบบจะส่งอีเมลเป็นรายการหัวข้อข่าวดิบ (ไม่มี AI สรุป) แทนที่จะไม่ส่งอะไรเลย
- เนื้อหาที่สรุปเป็นการรายงานข่าว **ไม่ใช่คำแนะนำการลงทุน** และ Gemini ถูกสั่งให้หลีกเลี่ยงการฟันธงทิศทางราคาหุ้น
