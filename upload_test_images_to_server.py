"""
upload_test_images_to_server.py
--------------------------------------------------------------------------
สคริปต์จำลองเป็น 'กล้อง ESP32' ยิงอัปโหลด 3 ภาพจริงเข้า Image Store Server:
- E100_0969_2582026_S1.png
- E100_0969_2582026_S2.png
- E100_0969_2582026_S3.png
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

def upload_3_shots_to_server():
    images = [
        "E100_0969_2582026_S1.png",
        "E100_0969_2582026_S2.png",
        "E100_0969_2582026_S3.png"
    ]

    print("=" * 65)
    print(f"🚀 กำลังส่ง 3 ภาพจริงขึ้น Server: {IMAGE_STORE_BASE_URL}")
    print("=" * 65)

    # เช็คว่าไฟล์ครบไหม
    for img_name in images:
        if not Path(img_name).exists():
            print(f"❌ ไม่พบไฟล์ภาพในเครื่อง: {img_name}")
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

        # 2. อัปโหลดภาพทั้ง 3 ภาพเข้าคิว
        UPLOAD_ENDPOINT = "/admin/images/upload"

        print(f"\n📤 2. กำลังอัปโหลดภาพ 3 ช็อตเข้าคิวบน Server...")
        for img_name in images:
            img_path = Path(img_name)
            files = {"file": (img_name, img_path.read_bytes(), "image/png")}
            data = {"filename": img_name}

            resp = httpx.post(
                f"{IMAGE_STORE_BASE_URL}{UPLOAD_ENDPOINT}",
                headers=headers,
                data=data,
                files=files,
                verify=IMAGE_STORE_VERIFY_TLS,
                timeout=15.0
            )
            
            if resp.status_code in [200, 201]:
                print(f"   ✅ อัปโหลดสำเร็จ: {img_name}")
            else:
                print(f"   ⚠️ อัปโหลด {img_name} (HTTP {resp.status_code}): {resp.text}")

        print("\n" + "=" * 65)
        print("🎉 อัปโหลดครบทั้ง 3 ภาพแล้ว! ตอนนี้ตัว Poller บน Server จะตรวจจับและเริ่มอ่านทันที")
        print("=" * 65)

    except Exception as exc:
        print(f"\n❌ ข้อผิดพลาด: {exc}")

if __name__ == "__main__":
    upload_3_shots_to_server()
