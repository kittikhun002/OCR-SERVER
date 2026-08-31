"""Meter Validator: Rule-Based Validation Engine for Industrial / Utility Meter Readings.

Rules implemented:
1. check_confidence: ตรวจสอบความมั่นใจของตัวเลขแต่ละหลัก (Confidence Threshold)
2. check_gear_consistency: ตรวจสอบความสัมพันธ์ของฟันเฟืองมิเตอร์ (Mechanical Gear Consistency)
3. check_reading_not_decreased: ค่าน้ำ/ไฟต้องไม่ลดลง (เดือนนี้ต้อง >= เดือนที่แล้ว)
4. check_usage_not_abnormal: อัตราการใช้ต้องไม่ผิดปกติ (เทียบกับค่าเฉลี่ย 3 เดือน)
5. validate_meter: ฟังก์ชันหลักที่รวมการตรวจทุกกฎเข้าด้วยกัน
"""

import sys

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def check_confidence(results, min_conf=0.60):
    """
    กฎข้อที่ 1: ตรวจสอบความมั่นใจ (Confidence)
    - ต้องไม่มีตัว '?'
    - ค่าความมั่นใจต้องไม่ต่ำกว่า min_conf
    """
    errors = []
    for r in results:
        pos = r.get("pos", "?")
        val = r.get("val_after", "?")
        conf = float(r.get("confidence", 0.0))

        if val == "?" or not str(val).isdigit():
            errors.append(f"[Rule 1] ช่องที่ {pos} อ่านค่าตัวเลขไม่ชัดเจน (ได้ค่า '{val}')")
        elif conf < min_conf:
            errors.append(f"[Rule 1] ช่องที่ {pos} ความมั่นใจต่ำ ({conf:.2f} < {min_conf:.2f})")

    return errors


def check_gear_consistency(results):
    """
    กฎข้อที่ 2: ตรวจสอบกลไกฟันเฟือง (Mechanical Gear Consistency)
    - ถ้าหลักซ้ายกำลังหมุน (is_transition) หลักขวาต้องเป็นเลข 8, 9, 0 หรือกำลังหมุนด้วย
    - ถ้าหลักซ้ายหมุน แต่หลักขวาเป็นเลข 1, 2, 3, 4, 5, 6, 7 นิ่งๆ -> ผิดธรรมชาติฟันเฟือง
    """
    errors = []
    n = len(results)

    for i in range(n - 1):
        curr_wheel = results[i]       # หลักซ้าย (เช่น หลักสิบ)
        right_wheel = results[i + 1]  # หลักขวา (เช่น หลักหน่วย)

        is_curr_trans = curr_wheel.get("is_transition", False)
        is_right_trans = right_wheel.get("is_transition", False)
        val_right = str(right_wheel.get("val_after", ""))

        # ถ้าหลักซ้ายกำลังหมุน
        if is_curr_trans:
            # แต่หลักขวาเพิ่งอยู่ที่เลข 1 ถึง 7 นิ่งๆ
            if val_right.isdigit() and int(val_right) in [1, 2, 3, 4, 5, 6, 7] and not is_right_trans:
                errors.append(
                    f"[Rule 2] ช่อง #{curr_wheel.get('pos', i+1)} กำลังหมุน {curr_wheel.get('display')} "
                    f"แต่ช่องขวา #{right_wheel.get('pos', i+2)} เพิ่งอยู่ที่เลข '{val_right}' (ขัดแย้งฟันเฟือง)"
                )

    return errors


# ===================================================================
# กฎข้อที่ 3: ค่าน้ำ/ไฟต้องไม่ลดลง (ประวัติเทียบค่าปัจจุบัน)
# ===================================================================
def check_reading_not_decreased(current_reading: float, history: list) -> list:
    """
    กฎข้อที่ 3: ค่ามิเตอร์ต้องไม่ลดลง
    - ค่าของเดือนนี้ต้องมากกว่าหรือเท่ากับเดือนที่แล้ว
    - history = ลิสต์ค่าย้อนหลัง เรียงจากเก่าไปใหม่ เช่น [1200.5, 1250.0, 1310.2]
      ตัวสุดท้าย (history[-1]) คือค่าเดือนที่แล้ว
    """
    errors = []
    if not history:
        return errors

    last_reading = history[-1]
    if current_reading < last_reading:
        errors.append(
            f"[Rule 3] ค่ามิเตอร์ลดลงผิดปกติ: "
            f"ค่าอ่านได้ = {current_reading:.1f} แต่เดือนที่แล้ว = {last_reading:.1f} "
            f"(ค่ามิเตอร์ถอยหลังไม่ได้)"
        )

    return errors


# ===================================================================
# กฎข้อที่ 4: อัตราการใช้ต้องไม่ผิดปกติ (เทียบค่าเฉลี่ย 3 เดือน)
# ===================================================================
USAGE_SPIKE_MULTIPLIER = float(__import__('os').getenv("USAGE_SPIKE_MULTIPLIER", "3.0"))


def check_usage_not_abnormal(current_reading: float, history: list, spike_multiplier: float = None) -> list:
    """
    กฎข้อที่ 4: ตรวจสอบว่าอัตราการใช้ไม่พุ่งผิดปกติ
    - คำนวณหน่วยที่ใช้แต่ละเดือนจากประวัติ 3 เดือน
    - หาค่าเฉลี่ยการใช้ต่อเดือน
    - ถ้าเดือนนี้ใช้เกินค่าเฉลี่ย x เท่า (ค่าเริ่มต้น 3 เท่า) → ผิดปกติ

    ตัวอย่าง: ประวัติ = [1200, 1250, 1310]
    - เดือน 1→2 ใช้ไป 50 หน่วย
    - เดือน 2→3 ใช้ไป 60 หน่วย
    - ค่าเฉลี่ยต่อเดือน = (50 + 60) / 2 = 55 หน่วย
    - ถ้าเดือนนี้อ่านได้ 1310 + 200 = 1510 → ใช้ไป 200 หน่วย
    - 200 > 55 x 3 (165) → ผิดปกติ!
    """
    if spike_multiplier is None:
        spike_multiplier = USAGE_SPIKE_MULTIPLIER

    errors = []
    if not history or len(history) < 2:
        return errors

    # คำนวณหน่วยที่ใช้แต่ละเดือนจากประวัติ
    monthly_usages = []
    for i in range(1, len(history)):
        usage = history[i] - history[i - 1]
        if usage >= 0:
            monthly_usages.append(usage)

    if not monthly_usages:
        return errors

    avg_usage = sum(monthly_usages) / len(monthly_usages)
    current_usage = current_reading - history[-1]

    # ถ้าค่าเฉลี่ยเป็น 0 (ไม่มีการใช้มา 3 เดือน) แต่เดือนนี้มีการใช้ → แจ้งเตือน
    if avg_usage == 0 and current_usage > 0:
        errors.append(
            f"[Rule 4] ผิดปกติ: 3 เดือนก่อนไม่มีการใช้งานเลย (เฉลี่ย = 0) "
            f"แต่เดือนนี้ใช้ไป {current_usage:.1f} หน่วย"
        )
        return errors

    if avg_usage > 0 and current_usage > avg_usage * spike_multiplier:
        errors.append(
            f"[Rule 4] อัตราการใช้ผิดปกติ: "
            f"เดือนนี้ใช้ไป {current_usage:.1f} หน่วย "
            f"แต่ค่าเฉลี่ย 3 เดือน = {avg_usage:.1f} หน่วย/เดือน "
            f"(เกิน {spike_multiplier:.0f} เท่าของค่าเฉลี่ย)"
        )

    return errors


# ===================================================================
# ฟังก์ชันหลัก: รวมทุกกฎเข้าด้วยกัน
# ===================================================================
def validate_meter(results, min_conf=0.60, current_reading: float = None, history: list = None):
    """
    ฟังก์ชันหลัก: รวมการตรวจทุกกฎเข้าด้วยกัน
    - results: ผลลัพธ์จาก YOLO + CNN (สำหรับ Rule 1 & 2)
    - current_reading: ค่ามิเตอร์ที่อ่านได้เดือนนี้ (สำหรับ Rule 3 & 4)
    - history: ประวัติค่ามิเตอร์ย้อนหลัง เช่น [1200.5, 1250.0, 1310.2] (สำหรับ Rule 3 & 4)
    Return: (is_valid: bool, errors: list)
    errors = []

    # ถ้าไม่พบช่องตัวเลขเลย ให้ถือว่าไม่ผ่านทันที
    if not results:
        return False, ["ไม่พบล้อตัวเลขในภาพเลย"]

    # Rule 1 & 2: ตรวจจากผล AI (Confidence + ฟันเฟือง)
    errors.extend(check_confidence(results, min_conf=min_conf))
    errors.extend(check_gear_consistency(results))

    # Rule 3 & 4: ตรวจจากประวัติ (ถ้ามี)
    if current_reading is not None and history:
        errors.extend(check_reading_not_decreased(current_reading, history))
        errors.extend(check_usage_not_abnormal(current_reading, history))

    is_valid = (len(errors) == 0)
    return is_valid, errors


if __name__ == "__main__":
    # ทดสอบกรณีตัวเลขปกติ (Pass)
    test_pass = [
        {"pos": 1, "val_after": "0", "confidence": 0.95, "is_transition": False, "display": "0"},
        {"pos": 2, "val_after": "4", "confidence": 0.88, "is_transition": False, "display": "4"},
        {"pos": 3, "val_after": "2", "confidence": 0.91, "is_transition": False, "display": "2"},
        {"pos": 4, "val_after": "8", "confidence": 0.85, "is_transition": False, "display": "8"},
    ]
    is_valid, errors = validate_meter(test_pass)
    print("ทดสอบปกติ:", "✅ PASS" if is_valid else "❌ FAIL", errors)

    # ทดสอบกรณีขัดแย้งฟันเฟือง (Fail)
    test_gear_fail = [
        {"pos": 1, "val_after": "0", "confidence": 0.95, "is_transition": False, "display": "0"},
        {"pos": 2, "val_after": "5", "confidence": 0.75, "is_transition": True, "display": "[4->5]"},
        {"pos": 3, "val_after": "4", "confidence": 0.90, "is_transition": False, "display": "4"},
    ]
    is_valid, errors = validate_meter(test_gear_fail)
    print("ทดสอบขัดแย้งฟันเฟือง:", "✅ PASS" if is_valid else "❌ FAIL", errors)

    print("\n" + "=" * 60)
    print("ทดสอบกฎข้อ 3 & 4 (ประวัติ 3 เดือน)")
    print("=" * 60)

    test_results = [
        {"pos": 1, "val_after": "1", "confidence": 0.95, "is_transition": False, "display": "1"},
        {"pos": 2, "val_after": "3", "confidence": 0.90, "is_transition": False, "display": "3"},
        {"pos": 3, "val_after": "5", "confidence": 0.88, "is_transition": False, "display": "5"},
        {"pos": 4, "val_after": "0", "confidence": 0.85, "is_transition": False, "display": "0"},
    ]
    history = [1200.0, 1250.0, 1310.0]

    # ทดสอบค่าปกติ (1350 > 1310 และอัตราการใช้ 40 ไม่เกิน 3 เท่าของเฉลี่ย 55)
    is_valid, errors = validate_meter(test_results, current_reading=1350.0, history=history)
    print(f"\nค่าปกติ (1350): {'✅ PASS' if is_valid else '❌ FAIL'}")
    for e in errors:
        print(f"  {e}")

    # ทดสอบค่าลดลง (1100 < 1310 → Rule 3 Fail)
    is_valid, errors = validate_meter(test_results, current_reading=1100.0, history=history)
    print(f"\nค่าลดลง (1100): {'✅ PASS' if is_valid else '❌ FAIL'}")
    for e in errors:
        print(f"  {e}")

    # ทดสอบอัตราการใช้ผิดปกติ (1600 - 1310 = 290, เฉลี่ย 55, เกิน 3 เท่า → Rule 4 Fail)
    is_valid, errors = validate_meter(test_results, current_reading=1600.0, history=history)
    print(f"\nอัตราผิดปกติ (1600): {'✅ PASS' if is_valid else '❌ FAIL'}")
    for e in errors:
        print(f"  {e}")
