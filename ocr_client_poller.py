"""
ocr_client_poller.py
--------------------------------------------------------------------------
ระบบ OCR Client Worker — เชื่อมต่อกับ Image Store Server (https://cfo.ntplc.co.th/iot)
ตามสเปกอัปเดตล่าสุด:

Flow การทำงาน:
1. GET  /admin/images/ocr?job_status=queued            -> ดึงคิวงาน
2. POST /admin/images/ocr/{job_id}/claim              -> Claim จองงาน และได้ image_file_urls ทั้งกลุ่ม burst
3. GET  /admin/images/{item_id}/file                  -> ดาวน์โหลดภาพเข้าโฟลเดอร์ downloads/
4. GET  /admin/meters/{meter_id}/ocr-readings         -> ดึงประวัติอ่านมิเตอร์ย้อนหลัง (capture_date/capture_time)
5. 🧠 Local AI (YOLO + CNN) + Rule-Based 4 กฎ         -> ประมวลผลและทำ Majority Vote
6. POST /admin/images/ocr/{job_id}/result             -> ส่งผลลัพธ์เหลือแค่ 2 field (ocr_reading, error_type)
"""

import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from main_pipeline import run_multi_image_pipeline, detect_meter_type

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

# ===================================================================
# 🔧 ตั้งค่าการเชื่อมต่อ Server (แก้ได้ใน .env)
# ===================================================================
IMAGE_STORE_BASE_URL = os.getenv("IMAGE_STORE_BASE_URL", "https://cfo.ntplc.co.th/iot").rstrip("/")
IMAGE_STORE_VERIFY_TLS = os.getenv("IMAGE_STORE_VERIFY_TLS", "true").lower() == "true"
OCR_API_KEY = os.getenv("OCR_API_KEY", "")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
REQUIRED_IMAGES_COUNT = int(os.getenv("REQUIRED_IMAGES_COUNT", "3"))

# ===================================================================
# 🛣️ API Paths
# ===================================================================
API_GET_JOBS           = "/admin/images/ocr"                      # GET  — poll หา job ที่ job_status=queued
API_CLAIM_JOB          = "/admin/images/ocr/{job_id}/claim"       # POST — claim job -> ได้ image_file_urls
API_GET_IMAGE_FILE     = "/admin/images/{item_id}/file"           # GET  — โหลดไฟล์ภาพ
API_GET_METER_READINGS = "/admin/meters/{meter_id}/ocr-readings"  # GET  — ดึงประวัติ ocr_meter ย้อนหลัง
API_SUBMIT_RESULT      = "/admin/images/ocr/{job_id}/result"      # POST — ส่งผลลัพธ์ (error_type, ocr_reading)
API_SUBMIT_FAIL        = "/admin/images/ocr/{job_id}/fail"        # POST — แจ้ง Technical/Network Error
# ===================================================================


def _auth_headers() -> dict:
    """Header ยืนยันตัวตนด้วย X-OCR-Key"""
    headers = {}
    if OCR_API_KEY:
        headers["X-OCR-Key"] = OCR_API_KEY
    return headers


def _request(method: str, path_or_url: str, **kwargs) -> httpx.Response:
    """ส่ง HTTP Request ไปยัง Image Store Server"""
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        url = path_or_url
    else:
        clean_path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
        url = f"{IMAGE_STORE_BASE_URL}{clean_path}"

    headers = kwargs.pop("headers", {})
    headers.update(_auth_headers())

    return httpx.request(
        method,
        url,
        headers=headers,
        verify=IMAGE_STORE_VERIFY_TLS,
        timeout=30.0,
        **kwargs,
    )


# -------------------------------------------------------------------
# 📦 Step 1: ดึงรายการงานที่รอทำ (Poll Job Queue)
# -------------------------------------------------------------------
def fetch_queued_jobs() -> list[dict]:
    """ดึงรายการ job ทั้งหมดที่มีสถานะ queued"""
    try:
        resp = _request("GET", API_GET_JOBS, params={"job_status": "queued", "limit": BATCH_SIZE})
        resp.raise_for_status()
        jobs = resp.json()
        return jobs if isinstance(jobs, list) else []
    except Exception as exc:
        print(f"⚠️ [Step 1] ดึงคิวงานไม่สำเร็จ: {exc}", flush=True)
        return []


# -------------------------------------------------------------------
# 🔒 Step 2 & 3: Claim งาน และดาวน์โหลดภาพเข้าโฟลเดอร์ downloads/
# -------------------------------------------------------------------
def claim_and_download_images(job: dict) -> tuple[bool, list[str]]:
    """
    Step 2: สั่ง Claim งานผ่าน POST /admin/images/ocr/{job_id}/claim
            เพื่อรับ image_file_urls ทั้งหมดของกลุ่ม burst
    Step 3: ดาวน์โหลดภาพทีละรูปเก็บไว้ใน downloads/
    """
    job_id = job["id"]
    download_dir = Path("downloads")
    download_dir.mkdir(parents=True, exist_ok=True)
    image_paths: list[str] = []

    try:
        # Step 2: Claim Job
        claim_resp = _request("POST", API_CLAIM_JOB.format(job_id=job_id))
        if claim_resp.status_code == 409:
            print(f"   ⚠️ งาน #{job_id} ถูกเครื่องอื่น Claim ไปแล้ว ข้าม", flush=True)
            return False, []
        claim_resp.raise_for_status()
        claim_data = claim_resp.json()

        image_urls = claim_data.get("image_file_urls", [])
        if not image_urls:
            # Fallback หากได้รูปเดียวจาก image_id
            image_id = job.get("image_id")
            if image_id:
                image_urls = [API_GET_IMAGE_FILE.format(item_id=image_id)]

        print(f"   🔒 Claim สำเร็จ: งาน #{job_id} (พบ {len(image_urls)} ภาพในกลุ่ม)", flush=True)

        # Step 3: ดาวน์โหลดแต่ละภาพเข้าโฟลเดอร์ downloads/
        for idx, img_url in enumerate(image_urls, start=1):
            file_resp = _request("GET", img_url)
            file_resp.raise_for_status()

            filename = f"job{job_id}_shot{idx}.jpg"
            img_path = download_dir / f"ocr_{filename}"
            img_path.write_bytes(file_resp.content)
            image_paths.append(str(img_path))
            print(f"      📥 ดาวน์โหลดรูปที่ {idx} บันทึกที่: {img_path} ({len(file_resp.content)/1024:.1f} KB)", flush=True)

        return True, image_paths

    except Exception as exc:
        print(f"   ❌ Claim หรือดาวน์โหลดภาพล้มเหลว: {exc}", flush=True)
        return False, []


# -------------------------------------------------------------------
# 📊 Step 4: ดึงประวัติการอ่านย้อนหลังของมิเตอร์
# -------------------------------------------------------------------
def fetch_meter_history(meter_id: str) -> list[float]:
    """
    ดึงประวัติการอ่านที่สำเร็จจาก GET /admin/meters/{meter_id}/ocr-readings?only_successful=true
    คืนค่าเป็น list ของ float ล่าสุด -> อดีต (เช่น [320.0, 310.0, 300.0])
    """
    try:
        resp = _request(
            "GET",
            API_GET_METER_READINGS.format(meter_id=meter_id),
            params={"limit": 5, "only_successful": "true"}
        )
        if resp.status_code == 200:
            entries = resp.json()
            history = []
            if isinstance(entries, list):
                for entry in entries:
                    reading_val = entry.get("ocr_reading")
                    # error_type 0 หรือ None ถือว่าสำเร็จ
                    err = entry.get("error_type")
                    if reading_val is not None and (err is None or err == 0):
                        try:
                            history.append(float(reading_val))
                        except (ValueError, TypeError):
                            pass
            print(f"   📊 ประวัติอ่านสำเร็จย้อนหลัง: {history}", flush=True)
            return history
        else:
            print(f"   ⚠️ ไม่พบประวัติเดิม (HTTP {resp.status_code}) — ข้ามไปทำต่อ", flush=True)
            return []
    except Exception as exc:
        print(f"   ⚠️ ดึงประวัติ Error: {exc} — ข้ามไปทำต่อ", flush=True)
        return []


# -------------------------------------------------------------------
# 🧠 Step 5 & 6: ประมวลผล OCR + ส่งผลกลับตามสเปก 2 ฟิลด์
# -------------------------------------------------------------------
def process_single_job(job: dict) -> None:
    """
    ประมวลผล 1 Job:
    Claim & โหลดรูป -> ดึงประวัติ -> OCR Pipeline -> ส่งผลผ่าน /result (error_type, ocr_reading)
    """
    job_id = job["id"]
    meter_id = job.get("meter_id") or job.get("original_filename", "")[:4]

    print(f"\n{'='*65}")
    print(f"🔍 [กำลังประมวลผล] งาน #{job_id} | มิเตอร์: '{meter_id}'")
    print(f"{'='*65}")

    # --- Step 2 & 3: Claim และโหลดรูปภาพ ---
    success, image_paths = claim_and_download_images(job)
    if not success or not image_paths:
        return

    # ตรวจสอบจำนวนภาพในกลุ่ม
    if len(image_paths) < REQUIRED_IMAGES_COUNT:
        print(f"⚠️ [Warn] งาน #{job_id} มีภาพ {len(image_paths)}/{REQUIRED_IMAGES_COUNT} ภาพ — ประมวลผลต่อด้วยภาพที่มี", flush=True)

    # --- Step 4: ดึงประวัติมิเตอร์ ---
    history = fetch_meter_history(meter_id)

    # 🏷️ ตรวจจับประเภทมิเตอร์ (elec, water, gas)
    meter_type = detect_meter_type(job.get("original_filename", meter_id))

    try:
        # --- Step 5: รัน Multi-image OCR Pipeline (Majority Voting) ---
        print(f"\n[Step 5] 🧠 กำลังอ่านภาพ AI (YOLO + CNN) + ตรวจ 4 กฎ ({len(image_paths)} รูป)...")

        pipeline_output = run_multi_image_pipeline(
            image_paths=image_paths,
            meter_type=meter_type,
            history=history,
            gemini_key=GEMINI_API_KEY,
        )

        status = pipeline_output.get("status")
        local_errors = pipeline_output.get("local_errors", [])
        raw_str = pipeline_output.get("raw", "0")

        # -------------------------------------------------------------------
        # 🏷️ แมป error_type เป็น Integer ตามสเปกใหม่ของ Server:
        # 0 = สำเร็จ (Success)
        # 1 = ภาพอ่านไม่ออก / ความมั่นใจต่ำ / เฟืองขัดแย้ง (image_unreadable)
        # 2 = ตรวจไม่พบตัวเลขในภาพ (no_digits_found)
        # 3 = ค่ามิเตอร์ลดลง หรือ การใช้พุ่งผิดปกติ (reading_decreased / usage_anomaly)
        # -------------------------------------------------------------------
        error_type: int = 0
        ocr_reading: float | None = None

        if status in ["APPROVED_LOCAL", "APPROVED_GEMINI"]:
            # ✅ เคส 0: สำเร็จ
            error_type = 0
            try:
                ocr_reading = float(raw_str)
            except ValueError:
                ocr_reading = float("".join(c for c in raw_str if c.isdigit() or c == ".") or "0")

        else:
            # ❌ เกิดข้อผิดพลาดทางธุรกิจ
            joined_errs = " ".join(local_errors).lower()

            if "ไม่พบล้อตัวเลข" in joined_errs or "no digits" in joined_errs:
                # เคส 2: no_digits_found (ห้ามส่ง ocr_reading)
                error_type = 2
                ocr_reading = None

            elif "ลดลง" in joined_errs or "decreased" in joined_errs or "ผิดปกติ" in joined_errs or "anomaly" in joined_errs or "พุ่งสูง" in joined_errs:
                # เคส 3: reading_decreased / usage_anomaly (ต้องส่ง ocr_reading)
                error_type = 3
                try:
                    ocr_reading = float(raw_str)
                except ValueError:
                    ocr_reading = None

            else:
                # เคส 1: image_unreadable (ห้ามส่ง ocr_reading)
                error_type = 1
                ocr_reading = None

        # --- Step 6: ส่งผลลัพธ์ผ่าน POST /admin/images/ocr/{job_id}/result ---
        print(f"\n[Step 6] 📤 กำลังส่งผลลัพธ์กลับ Server...")

        # เตรียม Form Data ส่งเฉพาะ 2 ฟิลด์ตามสเปกใหม่ (ส่ง error_type เป็น int)
        form_data = {
            "error_type": error_type,
        }
        if ocr_reading is not None and error_type in (0, 3):
            form_data["ocr_reading"] = ocr_reading

        submit_resp = _request(
            "POST",
            API_SUBMIT_RESULT.format(job_id=job_id),
            data=form_data,
        )
        submit_resp.raise_for_status()

        print(f"✅ งาน #{job_id} (มิเตอร์ '{meter_id}') เสร็จสมบูรณ์!", flush=True)
        print(f"   • ผลลัพธ์: error_type={error_type} ({'สำเร็จ' if error_type == 0 else f'Error Case {error_type}'}) | ocr_reading={ocr_reading}", flush=True)

    except Exception as exc:
        print(f"❌ เกิดข้อผิดพลาดกับงาน #{job_id}: {exc}", flush=True)
        try:
            _request("POST", API_SUBMIT_FAIL.format(job_id=job_id), json={"error": str(exc)[:2000]})
        except Exception:
            pass


# -------------------------------------------------------------------
# 🚀 Loop หลัก — ทำงานวนซ้ำตลอดเวลา
# -------------------------------------------------------------------
def run_forever() -> None:
    if not OCR_API_KEY:
        print("⚠️  กรุณาตั้งค่า OCR_API_KEY ในไฟล์ .env ก่อนเริ่มทำงาน (Container เปิดสแตนด์บายอยู่)", flush=True)
        while not OCR_API_KEY:
            time.sleep(30)

    print("=" * 65, flush=True)
    print(f"🚀 [ocr-client] เริ่มทำงาน เชื่อมต่อ: {IMAGE_STORE_BASE_URL}", flush=True)
    print(f"⏱️  ตรวจสอบคิวงานทุกๆ {POLL_INTERVAL_SECONDS} วินาที...", flush=True)
    print("=" * 65, flush=True)

    while True:
        try:
            jobs = fetch_queued_jobs()
            if jobs:
                print(f"\n📋 [Step 1] พบคิวงานใหม่ {len(jobs)} รายการ", flush=True)
                for job in jobs:
                    process_single_job(job)
            else:
                pass

        except Exception as exc:
            print(f"[ocr-client] ข้อผิดพลาดขณะวนลูป: {exc}", flush=True)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()