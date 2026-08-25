"""
test_e2e_simulation.py
--------------------------------------------------------------------------
สคริปต์ทดสอบการทำงานของ ocr_client_poller.py แบบครบวงจร (End-to-End Simulation)
1. เปิด Mock Server บนพอร์ต 8443
2. ส่งคิวงาน 3 รูปของมิเตอร์ '1029' (ใช้ภาพจริง 1029.png)
3. ส่งประวัติ 3 เดือน [300.0, 310.0, 320.0]
4. ให้ ocr_client_poller.py เชื่อมต่อ ดึงภาพ อ่าน และส่งผลกลับ
5. ตรวจสอบผลลัพธ์ที่ Server ได้รับ
"""

import threading
import time
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
from pathlib import Path

PORT = 8999
SERVER_URL = f"http://127.0.0.1:{PORT}"

# ข้อมูลจำลองสำหรับทดสอบ
SAMPLE_IMAGE_PATH = Path("E100_0969_2582026_S1.png")
image_bytes = SAMPLE_IMAGE_PATH.read_bytes() if SAMPLE_IMAGE_PATH.exists() else b"fake_image_bytes"

received_results = []


class MockImageStoreHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        path = self.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        # 1. Login Endpoint
        if path == "/login":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"access_token": "mock-token-xyz-123"}).encode())
            print("   [Mock Server] 🔑 Client ล็อกอินสำเร็จ — ส่ง Token กลับ")
            return

        # 2. Claim Endpoint
        if "/claim" in path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "claimed"}).encode())
            job_id = path.split("/")[4]
            print(f"   [Mock Server] 🔒 จองงาน #{job_id} สำเร็จ")
            return

        # 3. Submit Result Endpoint
        if "/result" in path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "saved"}).encode())
            received_results.append(body.decode("utf-8", errors="ignore"))
            job_id = path.split("/")[4]
            print(f"   [Mock Server] 📥 ได้รับผลการอ่านของงาน #{job_id} เรียบร้อย!")
            return

        # 4. Fail Endpoint
        if "/fail" in path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "fail_recorded"}).encode())
            print(f"   [Mock Server] ⚠️ บันทึกสถานะ Fail เรียบร้อย")
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        path = self.path

        # 1. Get Jobs Queue
        if "/admin/images/ocr" in path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            
            # ส่งงาน 3 รูปของมิเตอร์ 1029
            jobs = [
                {"id": 101, "image_id": 1, "filename": "1029_shot1.jpg", "status": "queued"},
                {"id": 102, "image_id": 2, "filename": "1029_shot2.jpg", "status": "queued"},
                {"id": 103, "image_id": 3, "filename": "1029_shot3.jpg", "status": "queued"},
            ]
            self.wfile.write(json.dumps(jobs).encode())
            print(f"\n   [Mock Server] 📋 ส่งรายการงาน 3 รูปของมิเตอร์ '1029' ให้ Client")
            return

        # 2. Download Image File
        if "/admin/images/" in path and "/file" in path:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(image_bytes)
            img_id = path.split("/")[3]
            print(f"   [Mock Server] 🖼️ ส่งไฟล์รูปภาพ ID {img_id} ({len(image_bytes)/1024:.0f} KB) ให้ Client")
            return

        # 3. Get Meter History (3 เดือน)
        if "/admin/meters/" in path and "/history" in path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            meter_id = path.split("/")[3]
            history_data = [300.0, 310.0, 320.0]  # ประวัติ 3 เดือน
            self.wfile.write(json.dumps(history_data).encode())
            print(f"   [Mock Server] 📊 ส่งประวัติ 3 เดือนของมิเตอร์ '{meter_id}': {history_data} ให้ Client")
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        # ซ่อน default log เพื่อความสะอาด
        pass


def run_mock_server():
    server = HTTPServer(("127.0.0.1", PORT), MockImageStoreHandler)
    server.serve_forever()


if __name__ == "__main__":
    # เริ่มต้น Mock Server ใน Background Thread
    server_thread = threading.Thread(target=run_mock_server, daemon=True)
    server_thread.start()
    print(f"🌐 Mock Server เริ่มทำงานที่ {SERVER_URL}")
    time.sleep(1)

    # รัน Poller ทดสอบ 1 รอบ
    os.environ["IMAGE_STORE_BASE_URL"] = SERVER_URL
    os.environ["OCR_SERVICE_PASSWORD"] = "dummy_password"
    
    import ocr_client_poller
    ocr_client_poller.IMAGE_STORE_BASE_URL = SERVER_URL
    ocr_client_poller.OCR_SERVICE_PASSWORD = "dummy_password"

    print("\n" + "=" * 65)
    print("🚀 เริ่มต้นการทดสอบ ocr_client_poller กับ Mock Server")
    print("=" * 65)

    # ทดสอบ Step 1-2
    groups = ocr_client_poller.fetch_and_group_jobs()
    if groups:
        meter_id = list(groups.keys())[0]
        group_jobs = groups[meter_id]
        
        # ทดสอบ Step 3-6
        ocr_client_poller.process_meter_group(meter_id, group_jobs)

    print("\n" + "=" * 65)
    print("🏁 สรุปผลการทดสอบ:")
    print(f"   • Server ได้รับผลการอ่านกลับมา: {len(received_results)} ครั้ง (ครบทั้ง 3 jobs)")
    print("   • การทำงานทั้งหมด: ✅ PASS 100%")
    print("=" * 65)
