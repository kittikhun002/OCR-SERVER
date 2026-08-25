"""
ocr_client_poller.py
--------------------------------------------------------------------------
รันฝั่ง "เครื่อง OCR" — เชื่อมต่อกับ image-store server อัตโนมัติ:

ไอเดียการทำงาน 6 สเต็ป:
1. เรียกขอรายชื่อไฟล์ (Job Queue) จาก Server
2. อ่าน 4 ตัวแรกของชื่อไฟล์ แล้วจัดกลุ่มใส่ array → เลือกกลุ่มแรก (array[0])
3. ดึงรูปภาพทั้งหมดของ ID ที่อยู่ในกลุ่มนั้น (เช่น 3 รูป ดึงทีละรูป)
4. ดึงประวัติย้อนหลัง 3 เดือนของ meter ID นั้น
5. นำรูปทั้งหมด + ประวัติ เข้า OCR Pipeline แล้วผ่าน Rule Base
6. ถ้าอ่านออกให้ส่งค่า meter กลับไปที่ Server
"""

import os
import sys
import time
import tempfile
from pathlib import Path
from collections import defaultdict

import httpx
from dotenv import load_dotenv

from main_pipeline import run_multi_image_pipeline, run_pipeline, detect_meter_type

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

# ===================================================================
# 🔧 ตั้งค่าการเชื่อมต่อ Server (แก้ได้ใน .env)
# ===================================================================
IMAGE_STORE_BASE_URL = os.getenv("IMAGE_STORE_BASE_URL", "https://localhost:8443")
IMAGE_STORE_VERIFY_TLS = os.getenv("IMAGE_STORE_VERIFY_TLS", "false").lower() == "true"
OCR_SERVICE_USERNAME = os.getenv("OCR_SERVICE_USERNAME", "ocr-service")
OCR_SERVICE_PASSWORD = os.getenv("OCR_SERVICE_PASSWORD", "")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "10"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
METER_ID_PREFIX_LEN = int(os.getenv("METER_ID_PREFIX_LEN", "4"))
REQUIRED_IMAGES_COUNT = int(os.getenv("REQUIRED_IMAGES_COUNT", "3"))  # ต้องมีครบ 3 รูปเท่านั้น

# ===================================================================
# 🛣️ API Paths — TODO: เปลี่ยน Path ตรงนี้ให้ตรงกับ Server จริง
# ===================================================================
API_LOGIN              = "/login"                              # POST — ล็อกอินรับ Token
API_GET_JOBS           = "/admin/images/ocr"                   # GET  — ดึงรายการงานที่รอ (Step 1)
API_CLAIM_JOB          = "/admin/images/ocr/{job_id}/claim"    # POST — จองงานกันซ้ำ
API_GET_IMAGE_FILE     = "/admin/images/{image_id}/file"       # GET  — ดาวน์โหลดไฟล์รูป (Step 3)
API_GET_METER_HISTORY  = "/admin/meters/{meter_id}/history"    # GET  — ดึงประวัติ 3 เดือน (Step 4)
API_SUBMIT_RESULT      = "/admin/images/ocr/{job_id}/result"   # POST — ส่งค่าอ่านกลับ (Step 6)
API_SUBMIT_FAIL        = "/admin/images/ocr/{job_id}/fail"     # POST — แจ้ง Error กลับ
# ===================================================================

_token: str | None = None


# -------------------------------------------------------------------
# 🔑 ระบบ Login & Request อัตโนมัติ (ไม่ต้องแก้ไข)
# -------------------------------------------------------------------
def _login() -> str:
    global _token
    resp = httpx.post(
        f"{IMAGE_STORE_BASE_URL}{API_LOGIN}",
        json={"username": OCR_SERVICE_USERNAME, "password": OCR_SERVICE_PASSWORD},
        verify=IMAGE_STORE_VERIFY_TLS,
        timeout=10.0,
    )
    resp.raise_for_status()
    _token = resp.json()["access_token"]
    return _token


def _auth_headers() -> dict:
    if _token is None:
        _login()
    return {"Authorization": f"Bearer {_token}"}


def _request(method: str, path: str, retry: bool = True, **kwargs) -> httpx.Response:
    resp = httpx.request(
        method,
        f"{IMAGE_STORE_BASE_URL}{path}",
        headers=_auth_headers(),
        verify=IMAGE_STORE_VERIFY_TLS,
        timeout=30.0,
        **kwargs,
    )
    if resp.status_code == 401 and retry:
        global _token
        _token = None
        return _request(method, path, retry=False, **kwargs)
    return resp


# -------------------------------------------------------------------
# 📦 Step 1 & 2: ดึงรายการงาน → จัดกลุ่มตาม 4 ตัวแรกของชื่อไฟล์
# -------------------------------------------------------------------
def fetch_and_group_jobs() -> dict[str, list[dict]]:
    """
    Step 1: ยิง GET ดึงรายการงานทั้งหมดที่สถานะ queued
    Step 2: อ่าน 4 ตัวแรกของชื่อไฟล์แล้วจัดกลุ่มเป็น dict
            เช่น {"1029": [job1, job2, job3], "e555": [job4, job5, job6]}
    """
    resp = _request("GET", API_GET_JOBS, params={"job_status": "queued", "limit": BATCH_SIZE})
    resp.raise_for_status()
    jobs = resp.json()

    if not jobs:
        return {}

    # จัดกลุ่มตาม prefix (4 ตัวแรกของชื่อไฟล์)
    groups: dict[str, list[dict]] = defaultdict(list)
    for job in jobs:
        filename = job.get("filename", "")
        prefix = filename[:METER_ID_PREFIX_LEN]  # อ่าน 4 ตัวแรก
        groups[prefix].append(job)

    print(f"\n📋 [Step 1-2] พบงานทั้งหมด {len(jobs)} รายการ, จัดกลุ่มได้ {len(groups)} มิเตอร์:", flush=True)
    for prefix, group_jobs in groups.items():
        print(f"   • มิเตอร์ '{prefix}' — {len(group_jobs)} รูป")

    return dict(groups)


# -------------------------------------------------------------------
# 🖼️ Step 3: ดึงรูปภาพทีละรูปของกลุ่มที่เลือก
# -------------------------------------------------------------------
def download_images(jobs: list[dict]) -> list[str]:
    """
    ดึงรูปภาพจาก Server ทีละรูป (เรียก API ตามจำนวนรูปในกลุ่ม)
    คืนค่าเป็นลิสต์ของ path ไฟล์ชั่วคราวที่บันทึกไว้ในเครื่อง
    """
    tmp_dir = Path(tempfile.gettempdir())
    image_paths: list[str] = []

    for job in jobs:
        job_id = job["id"]
        image_id = job["image_id"]
        filename = job.get("filename", f"{image_id}.jpg")

        # จองงานกันเครื่องอื่นแย่ง
        claim_resp = _request("POST", API_CLAIM_JOB.format(job_id=job_id))
        if claim_resp.status_code == 409:
            print(f"   ⚠️ งาน #{job_id} ถูกเครื่องอื่นจองไปแล้ว ข้าม", flush=True)
            continue
        claim_resp.raise_for_status()

        # ดาวน์โหลดไฟล์ภาพ
        file_resp = _request("GET", API_GET_IMAGE_FILE.format(image_id=image_id))
        file_resp.raise_for_status()

        tmp_path = tmp_dir / f"ocr_{job_id}_{filename}"
        tmp_path.write_bytes(file_resp.content)
        image_paths.append(str(tmp_path))

        print(f"   📥 ดาวน์โหลดรูปสำเร็จ: {filename} ({len(file_resp.content) / 1024:.0f} KB)", flush=True)

    return image_paths


# -------------------------------------------------------------------
# 📊 Step 4: ดึงประวัติย้อนหลัง 3 เดือนของมิเตอร์
# -------------------------------------------------------------------
def fetch_meter_history(meter_id: str) -> list:
    """
    ดึงประวัติค่าอ่าน 3 เดือนย้อนหลังจาก Server
    คืนค่าเป็น list เช่น [1200.5, 1250.0, 1310.2]
    ถ้าดึงไม่ได้จะคืนค่า list เปล่า (ไม่ block การทำงาน)
    """
    try:
        resp = _request("GET", API_GET_METER_HISTORY.format(meter_id=meter_id))
        if resp.status_code == 200:
            data = resp.json()
            # TODO: ปรับให้ตรงกับโครงสร้าง JSON จริงที่ Server ส่งกลับมา
            # เช่นอาจเป็น data["history"] หรือ data["readings"] หรือเป็น list ตรงๆ
            history = data if isinstance(data, list) else data.get("history", [])
            print(f"   📊 ประวัติ 3 เดือน: {history}", flush=True)
            return history
        else:
            print(f"   ⚠️ ดึงประวัติไม่ได้ (HTTP {resp.status_code}) — ข้ามไปทำต่อ", flush=True)
            return []
    except Exception as exc:
        print(f"   ⚠️ ดึงประวัติ Error: {exc} — ข้ามไปทำต่อ", flush=True)
        return []


# -------------------------------------------------------------------
# 🧠 Step 5 & 6: ประมวลผล OCR + Rule Base → ส่งผลกลับ Server
# -------------------------------------------------------------------
def process_meter_group(meter_id: str, jobs: list[dict]) -> None:
    """
    ประมวลผลมิเตอร์ 1 ตัว (หลายรูป) ตามลำดับ:
    Step 3: โหลดรูป → Step 4: ดึงประวัติ → Step 5: OCR → Step 6: ส่งผล
    """
    print(f"\n{'='*60}")
    print(f"🔍 [กำลังประมวลผล] มิเตอร์ ID: '{meter_id}' ({len(jobs)} รูป)")
    print(f"{'='*60}")

    # --- Step 3: ดึงรูปภาพทีละรูป ---
    print(f"\n[Step 3] 🖼️ กำลังดาวน์โหลดรูปภาพ...")
    image_paths = download_images(jobs)

    # ตรวจสอบว่าต้องมีครบตามจำนวนที่กำหนด (เช่น 3 รูป)
    if len(image_paths) < REQUIRED_IMAGES_COUNT:
        print(f"⚠️ [Skip] มิเตอร์ '{meter_id}' ได้รูปเพียง {len(image_paths)}/{REQUIRED_IMAGES_COUNT} รูป (ไม่ครบ {REQUIRED_IMAGES_COUNT} รูป) → ทิ้งงานนี้และข้ามไป", flush=True)
        for p in image_paths:
            Path(p).unlink(missing_ok=True)
        return

    # --- Step 4: ดึงประวัติ 3 เดือน ---
    print(f"\n[Step 4] 📊 กำลังดึงประวัติย้อนหลัง 3 เดือน...")
    history = fetch_meter_history(meter_id)

    # 🏷️ ตรวจประเภทมิเตอร์จากชื่อไฟล์อัตโนมัติ (e -> elec, w -> water)
    meter_type = detect_meter_type(jobs[0].get("filename", ""))

    try:
        # --- Step 5: ประมวลผล OCR + Rule Base (Majority Voting จาก 3 รูป) ---
        print(f"\n[Step 5] 🧠 กำลังประมวลผล OCR Pipeline ({len(image_paths)} รูป)...")

        pipeline_output = run_multi_image_pipeline(
            image_paths=image_paths,
            meter_type=meter_type,
            history=history,
            gemini_key=GEMINI_API_KEY,
        )

        # --- Step 6: จัดฟอร์แมตผลลัพธ์แล้วส่งกลับ Server ---
        status = pipeline_output.get("status")

        if status == "APPROVED_LOCAL":
            raw_str = pipeline_output.get("raw", "0")
            reading = float(raw_str) if raw_str.replace(".", "", 1).isdigit() else 0.0
            raw_text = f"{pipeline_output.get('reading')} [อนุมัติโดย Local AI]"
        elif status == "APPROVED_GEMINI":
            reading_val = pipeline_output.get("reading", "0")
            reading = float("".join(c for c in reading_val if c.isdigit() or c == ".") or "0")
            raw_text = f"{reading_val} [อนุมัติโดย Gemini Vision: {pipeline_output.get('reason', '')}]"
        else:
            errors = pipeline_output.get("local_errors", [])
            raw_text = f"รอตรวจสอบ [ส่งต่อให้คนตรวจ: {'; '.join(errors)}]"
            reading = 0.0

        print(f"\n[Step 6] 📤 กำลังส่งผลลัพธ์กลับ Server...")

        # ส่งผลลัพธ์ทุก job ในกลุ่มนี้
        for job in jobs:
            job_id = job["id"]

            # หาภาพ Debug ถ้ามี
            first_img = Path(image_paths[0])
            debug_img_candidate = Path("review") / f"cnn_{first_img.name}"
            upload_img_path = debug_img_candidate if debug_img_candidate.exists() else first_img

            result_resp = _request(
                "POST", API_SUBMIT_RESULT.format(job_id=job_id),
                data={"reading": reading, "raw_text": raw_text},
                files={"result_image": (upload_img_path.name, upload_img_path.read_bytes(), "image/jpeg")},
            )
            result_resp.raise_for_status()

        print(f"✅ มิเตอร์ '{meter_id}' เสร็จสมบูรณ์: reading={reading} ({status})", flush=True)

    except Exception as exc:
        # แจ้ง Error กลับ Server ทุก job ในกลุ่ม
        for job in jobs:
            try:
                _request(
                    "POST", API_SUBMIT_FAIL.format(job_id=job["id"]),
                    json={"error": str(exc)[:500]},
                )
            except Exception:
                pass
        print(f"❌ มิเตอร์ '{meter_id}' ล้มเหลว: {exc}", flush=True)

    finally:
        # ลบไฟล์ชั่วคราวทิ้ง
        for p in image_paths:
            Path(p).unlink(missing_ok=True)


# -------------------------------------------------------------------
# 🚀 Loop หลัก — ทำงานวนซ้ำตลอดเวลา
# -------------------------------------------------------------------
def run_forever() -> None:
    if not OCR_SERVICE_PASSWORD:
        print("⚠️  กรุณาตั้งค่า OCR_SERVICE_PASSWORD ในไฟล์ .env ก่อนเริ่มทำงาน (Container ยังคงเปิดสแตนด์บายอยู่)", flush=True)
        while not OCR_SERVICE_PASSWORD:
            time.sleep(30)

    print(f"🚀 [ocr-client] เริ่มทำงาน — ตรวจสอบงานใหม่ทุกๆ {POLL_INTERVAL_SECONDS} วินาที...", flush=True)
    while True:
        try:
            # Step 1 & 2: ดึงรายการงาน + จัดกลุ่มตาม 4 ตัวแรกของชื่อไฟล์
            groups = fetch_and_group_jobs()

            if groups:
                # ค้นหากลุ่มมิเตอร์ที่มีรูปครบตามเกณฑ์ (อย่างน้อย REQUIRED_IMAGES_COUNT รูป)
                processed_any = False
                for meter_id, group_jobs in groups.items():
                    if len(group_jobs) >= REQUIRED_IMAGES_COUNT:
                        # Step 3-6: โหลดรูป → ดึงประวัติ → OCR → ส่งผลกลับ
                        process_meter_group(meter_id, group_jobs)
                        processed_any = True
                        break  # ประมวลผลทีละ 1 มิเตอร์ต่อรอบ
                    else:
                        print(f"⏳ [Pending] มิเตอร์ '{meter_id}' มีรูปในคิวเพียง {len(group_jobs)}/{REQUIRED_IMAGES_COUNT} รูป (ยังไม่ครบ 3 รูป) → ข้ามเพื่อรอคิวให้ครบ", flush=True)

        except Exception as exc:
            print(f"[ocr-client] ข้อผิดพลาดขณะดึงงาน: {exc}", flush=True)

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()