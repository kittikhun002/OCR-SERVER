"""
test_fetch_filenames.py
--------------------------------------------------------------------------
สคริปต์ทดสอบดึงคิวงานจาก Image Store Server จริง (https://cfo.ntplc.co.th/iot)
ผ่าน Header X-OCR-Key
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

IMAGE_STORE_BASE_URL = os.getenv("IMAGE_STORE_BASE_URL", "https://cfo.ntplc.co.th/iot").rstrip("/")
IMAGE_STORE_VERIFY_TLS = os.getenv("IMAGE_STORE_VERIFY_TLS", "true").lower() == "true"
OCR_API_KEY = os.getenv("OCR_API_KEY", "")

def test_fetch():
    print("=" * 65)
    print("🔍 กำลังทดสอบดึงคิวงานจาก Image Store Server...")
    print(f"🌐 Server URL: {IMAGE_STORE_BASE_URL}")
    print(f"🔑 X-OCR-Key:  {'*' * len(OCR_API_KEY) if OCR_API_KEY else '(ไม่ได้ระบุ)'}")
    print("=" * 65)

    headers = {}
    if OCR_API_KEY:
        headers["X-OCR-Key"] = OCR_API_KEY

    try:
        url = f"{IMAGE_STORE_BASE_URL}/admin/images/ocr"
        print(f"\n📡 ยิง GET {url}?job_status=queued ...")
        
        resp = httpx.get(
            url,
            params={"job_status": "queued", "limit": 10},
            headers=headers,
            verify=IMAGE_STORE_VERIFY_TLS,
            timeout=15.0
        )
        print(f"📥 สถานะ HTTP Response: {resp.status_code}")

        if resp.status_code == 200:
            jobs = resp.json()
            print(f"\n✅ เชื่อมต่อสำเร็จ! พบคิวงานที่รออยู่: {len(jobs)} งาน")
            for idx, job in enumerate(jobs, start=1):
                print(f"   {idx}. Job ID: #{job.get('id')} | Meter ID: {job.get('meter_id')} | File: {job.get('original_filename')}")
        elif resp.status_code in (401, 403):
            print(f"\n❌ เข้าใช้งานไม่ได้ (HTTP {resp.status_code}): ตรวจสอบ OCR_API_KEY ในไฟล์ .env")
        else:
            print(f"\n⚠️ ได้รับ Response: {resp.text}")

    except Exception as exc:
        print(f"\n❌ เกิดข้อผิดพลาดในการเชื่อมต่อ: {exc}")

if __name__ == "__main__":
    test_fetch()
