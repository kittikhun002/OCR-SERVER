"""
upload_test_images_to_server.py
--------------------------------------------------------------------------
สคริปต์จำลองเป็น 'กล้อง ESP32' ยิงอัปโหลดรูปภาพ 3 รูปขึ้น Server จริง:
1. อ่านรูปจากเครื่องเรา (เช่น 1029.png)
2. อัปโหลดขึ้น Server 3 ครั้งด้วยชื่อ:
   - 1029_shot1.jpg
   - 1029_shot2.jpg
   - 1029_shot3.jpg
3. เมื่อขึ้น Server แล้ว ocr_client_poller ที่รันอยู่จะตรวจจับได้ทันที!
"""

import os
import sys
from pathlib import Path
import httpx
from dotenv import load_dotenv

load_dotenv()

IMAGE_STORE_BASE_URL = os.getenv("IMAGE_STORE_BASE_URL", "https://localhost:8443")
IMAGE_STORE_VERIFY_TLS = os.getenv("IMAGE_STORE_VERIFY_TLS", "false").lower() == "true"
OCR_SERVICE_USERNAME = os.getenv("OCR_SERVICE_USERNAME", "ocr-service")
OCR_SERVICE_PASSWORD = os.getenv("OCR_SERVICE_PASSWORD", "")

def login_and_upload(meter_id="1029", image_file="1029.png"):
    print("=" * 65)
    print(f"🚀 กำลังจำลองเป็นกล้อง ส่งภาพ 3 รูปของมิเตอร์ '{meter_id}' ขึ้น Server...")
    print(f"🌐 Server URL: {IMAGE_STORE_BASE_URL}")
    print("=" * 65)

    img_path = Path(image_file)
    if not img_path.exists():
        print(f"❌ ไม่พบไฟล์ภาพ: {img_path}")
        return

    try:
        # 1. Login ขอ Token
        print("\n🔑 1. กำลังล็อกอินเข้า Server...")
        login_resp = httpx.post(
            f"{IMAGE_STORE_BASE_URL}/login",
            json={"username": OCR_SERVICE_USERNAME, "password": OCR_SERVICE_PASSWORD},
            verify=IMAGE_STORE_VERIFY_TLS,
            timeout=10.0
        )
        login_resp.raise_for_status()
        token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print("   ✅ ล็อกอินสำเร็จ")

        # 2. อัปโหลดภาพ 3 ภาพขึ้น Server
        # (TODO: ปรับ endpoint อัปโหลดให้ตรงกับ API Server เช่น /admin/images/upload หรือ /api/upload)
        UPLOAD_ENDPOINT = "/admin/images/upload"

        print(f"\n📤 2. กำลังอัปโหลดภาพ 3 ภาพเข้าคิว...")
        for i in range(1, 4):
            filename = f"{meter_id}_shot{i}.jpg"
            files = {"file": (filename, img_path.read_bytes(), "image/jpeg")}
            data = {"meter_id": meter_id, "filename": filename}

            resp = httpx.post(
                f"{IMAGE_STORE_BASE_URL}{UPLOAD_ENDPOINT}",
                headers=headers,
                data=data,
                files=files,
                verify=IMAGE_STORE_VERIFY_TLS,
                timeout=15.0
            )
            
            if resp.status_code in [200, 201]:
                print(f"   ✅ อัปโหลดสำเร็จ: {filename}")
            else:
                print(f"   ⚠️ อัปโหลด {filename} (HTTP {resp.status_code}): {resp.text}")

        print("\n" + "=" * 65)
        print("🎉 อัปโหลดครบ 3 ภาพแล้ว! ตอนนี้ตัว Poller บน Server จะเริ่มดึงไปอ่านอัตโนมัติ")
        print("=" * 65)

    except Exception as exc:
        print(f"\n❌ ข้อผิดพลาด: {exc}")

if __name__ == "__main__":
    login_and_upload()
