"""Gemini Verifier: Secondary AI Verification for Ambiguous Meter Readings.

ส่งรูปภาพมิเตอร์ไปถาม Gemini Vision เมื่อ Local AI ไม่ผ่านเกณฑ์ Rule-Base
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def verify_with_gemini(image_path: str, error_notes: str = "", api_key: str = None) -> dict:
    """
    ส่งภาพมิเตอร์และข้อความแจ้งปัญหาไปให้ Gemini Vision ช่วยอ่านซ้ำ
    Return: dict {"success": bool, "reading": str, "reason": str, "error": str}
    """
    api_key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        return {"success": False, "error": "ไม่ได้ตั้งค่า GEMINI_API_KEY"}

    image_path = Path(image_path)
    if not image_path.exists():
        return {"success": False, "error": f"ไม่พบไฟล์ภาพ: {image_path}"}

    prompt = f"""You are an expert utility meter inspector (Electric, Water, and Gas meters).
Our automated computer vision system tried to read this meter image, but encountered uncertainties:
[Issues]: {error_notes}

First, identify the meter type and verify its STRICT integer + decimal wheel structure:
- Electric meter (kWh): MUST have EXACTLY 5 digits total = 4 integer digits (before decimal, black/white wheels) + 1 decimal digit (after decimal, red/framed wheel on the right).
  Example: integer "0354" + decimal "8" -> meter_reading "03548".
- Gas meter (m3 / dm3): MUST have EXACTLY 8 digits total = 5 integer digits (before decimal, black wheels) + 3 decimal digits (after decimal, red wheels on the right).
  Example: integer "00123" + decimal "456" -> meter_reading "00123456".
- Water meter: 4, 5, 7, or 8 digits total (read all rectangular roller wheels; ignore circular analog pointer sub-dials).

Instructions:
1. Read ONLY the main horizontal mechanical digit wheels from left to right. Ignore serial numbers, model codes, ratings (e.g. 220V, 5(15)A, G4, kWh), barcodes, and circular pointer sub-dials.
2. Separate the reading into integer wheels (before decimal) and decimal wheels (red/framed wheels after decimal) to verify you did NOT miss any wheel on the left or right.
3. CRITICAL: DO NOT INSERT ANY DOTS OR DECIMAL POINTS in "meter_reading". Concatenate integer_part + decimal_part as continuous digits ONLY (NEVER "0354.8" or "00123.456").
4. CRITICAL: DO NOT OMIT LEADING ZEROS in integer_part or meter_reading.
5. If a wheel is halfway rolling between two digits, check the wheel immediately to its right: if the right wheel shows 0-2 (just rolled over), pick the higher digit; if it shows 7-9 (not yet rolled over), pick the lower digit.
6. If any wheel is completely unreadable (glare/blur/obstructed), use "?" for that position instead of guessing.
7. Return ONLY valid JSON format without markdown code blocks.

Format:
{{
  "meter_type": "elec",
  "integer_part": "0354",
  "decimal_part": "8",
  "meter_reading": "03548",
  "reasoning": "Electric meter: 4 black integer dials (0 3 5 4) + 1 red decimal dial (8)."
}}
"""

    try:
        # พยายามใช้ google-genai หรือ fallback เป็น google-generativeai
        model_name = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    genai.types.Part.from_bytes(
                        data=image_path.read_bytes(),
                        mime_type="image/jpeg" if image_path.suffix.lower() in [".jpg", ".jpeg"] else "image/png"
                    ),
                    prompt
                ]
            )
            text = response.text.strip()
        except ImportError:
            import google.generativeai as genai
            import PIL.Image
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
            img = PIL.Image.open(image_path)
            response = model.generate_content([prompt, img])
            text = response.text.strip()

        # ลบ markdown code block ออกและสกัด JSON ก้อนหลักอย่างปลอดภัย
        text = text.replace("```json", "").replace("```", "").strip()
        start_idx = text.find("{")
        end_idx = text.rfind("}")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            data = json.loads(text[start_idx:end_idx + 1])
        else:
            data = json.loads(text)

        return {
            "success": True,
            "reading": str(data.get("meter_reading", "")),
            "reason": str(data.get("reasoning", "")),
            "raw_response": text
        }

    except Exception as e:
        return {"success": False, "error": f"Gemini Error: {str(e)}"}


if __name__ == "__main__":
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        print("💡 Hint: ยังไม่ได้ตั้งค่า GEMINI_API_KEY ใน Environment หรือ .env")
        sys.exit(1)

    print("✅ GEMINI_API_KEY พร้อมใช้งาน")

    if len(sys.argv) > 1:
        test_img = sys.argv[1]
        error_notes = sys.argv[2] if len(sys.argv) > 2 else "CLI manual test"
        print(f"\n🔍 กำลังส่งภาพไปวิเคราะห์: {test_img}")
        if error_notes:
            print(f"📝 หมายเหตุปัญหา: {error_notes}")

        result = verify_with_gemini(test_img, error_notes=error_notes)

        print("\n" + "=" * 50)
        if result.get("raw_response"):
            print("📩 Raw Response จาก Gemini:")
            print(result["raw_response"])
            print("=" * 50)

        print("📊 ผลลัพธ์ที่แปลงแล้ว (Parsed Result):")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("=" * 50)
    else:
        print("\n💡 วิธีสั่งรันเพื่อทดสอบภาพมิเตอร์:")
        print("   python gemini_verifier.py <path_to_image> [ข้อความแจ้งปัญหา]")
        print("ตัวอย่าง:")
        print("   python gemini_verifier.py /Users/macbookairm1/Downloads/YOLO/elec/m14.png")
        print('   python gemini_verifier.py /Users/macbookairm1/Downloads/YOLO/elec/m14.png "หลักที่ 3 เบลอ"')
