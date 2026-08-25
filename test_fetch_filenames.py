"""
test_fetch_filenames.py
--------------------------------------------------------------------------
ทดสอบเฉพาะ Step 1 & 2:
1. เชื่อมต่อ Server ตามค่าใน .env
2. ยิงขอคิวงานเพื่อดึง 'รายชื่อไฟล์' (filename) มาดู
3. ตัด 4 ตัวแรกของชื่อไฟล์และจัดกลุ่มให้ดู
(สคริปต์นี้จะไม่ดาวน์โหลดรูปภาพ และจะไม่รัน OCR ครับ)
"""

import os
import sys
import json
from dotenv import load_dotenv
from ocr_client_poller import _login, _request, API_GET_JOBS, BATCH_SIZE, METER_ID_PREFIX_LEN
from collections import defaultdict

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

def test_fetch_only_filenames():
    print("=" * 65)
    print("🔍 กำลังทดสอบดึงรายชื่อไฟล์จาก Server...")
    print(f"🌐 Server URL: {os.getenv('IMAGE_STORE_BASE_URL')}")
    print("=" * 65)

    try:
        # 1. ทดสอบ Login
        print("\n[Step 0] 🔑 กำลังล็อกอินเข้า Server...")
        token = _login()
        print(f"   ✅ ล็อกอินสำเร็จ (Token: {token[:15]}...)")

        # 2. ทดสอบขอรายชื่อไฟล์
        print(f"\n[Step 1] 📋 กำลังยิงขอรายการงานจาก '{API_GET_JOBS}'...")
        resp = _request("GET", API_GET_JOBS, params={"job_status": "queued", "limit": BATCH_SIZE})
        resp.raise_for_status()
        jobs = resp.json()

        print(f"   📥 ได้รับข้อมูลตอบกลับมาจาก Server: {len(jobs)} รายการ")
        
        if not jobs:
            print("\n⚠️ ตอนนี้ยังไม่มีงานในคิวบน Server (คิวว่างเปล่า)")
            return

        print("\n--- 📄 รายชื่อไฟล์ดิบที่ได้มาจาก Server ---")
        for i, job in enumerate(jobs, 1):
            job_id = job.get("id")
            filename = job.get("filename", "ไม่มีชื่อไฟล์")
            image_id = job.get("image_id")
            print(f"   {i}. Job ID #{job_id} | Image ID: {image_id} | Filename: '{filename}'")

        # 3. ทดสอบตัด 4 ตัวแรกเพื่อจัดกลุ่ม
        print(f"\n[Step 2] ✂️  ทดสอบตัด {METER_ID_PREFIX_LEN} ตัวแรกของชื่อไฟล์เพื่อจัดกลุ่มมิเตอร์:")
        groups = defaultdict(list)
        for job in jobs:
            filename = job.get("filename", "")
            prefix = filename[:METER_ID_PREFIX_LEN]
            groups[prefix].append(job)

        for prefix, group_jobs in groups.items():
            filenames_in_group = [j.get("filename") for j in group_jobs]
            print(f"\n   🏷️  กลุ่มรหัสมิเตอร์ '{prefix}': มี {len(group_jobs)} รูป")
            for fn in filenames_in_group:
                print(f"      - {fn}")

        print("\n" + "=" * 65)
        print("✅ ทดสอบดึงรายชื่อไฟล์และจัดกลุ่มสำเร็จเรียบร้อย!")
        print("=" * 65)

    except Exception as exc:
        print(f"\n❌ เกิดข้อผิดพลาด: {exc}")

if __name__ == "__main__":
    test_fetch_only_filenames()
