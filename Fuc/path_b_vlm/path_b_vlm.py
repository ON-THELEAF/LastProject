"""B경로: VLM(Qwen) 질문 프롬프트·모델 불러오기  (bridgedocs.py에서 나눔)"""
from pathlib import Path


# 주의: fp16으로 돌리면 문서 1장 응답이 통째로 "!!!!"로 깨진 적이 있다(실측). bf16으로 부른다.

HERE = Path(__file__).parent

# ───────── 프롬프트 3단계 ─────────
# v1: 기존(기준선) / v2: 형식 강제 + 필드 의미 명확화(서식 위치 힌트 없음) / v3: v2 + 서식 위치 힌트(서식을 아는 경우의 최선, 낙관적)
V1 = {
    "registry": {
        "property_address": "이 등기사항증명서 맨 위 대괄호 줄에 적힌 부동산 소재지를 그대로 적어줘. '[집합건물]'은 빼고 주소만 답해.",
        "owner_name": "갑구(소유권에 관한 사항)에서 가장 마지막 소유자의 이름이 뭐야? 이름만 답해.",
        "ownership_change_date": "갑구에서 가장 마지막 소유자 행의 접수일이 언제야? YYYY-MM-DD 형식으로만 답해.",
        "seizure_flag": "갑구에 압류 또는 가압류 등기가 있어? '있음' 또는 '없음'으로만 답해.",
        "mortgage_amount": "을구에서 근저당권의 채권최고액이 얼마야? 숫자와 '원'만 답해. 근저당이 없으면 '없음'이라고만 답해.",
        "mortgage_holder": "을구에서 근저당권자가 누구야? 이름만 답해. 근저당이 없으면 '없음'이라고만 답해.",
    },
    "lease": {
        "lessor_name": "맨 위 '임대인( )과 임차인( )' 문장에서 임대인 이름이 뭐야? 이름만 답해.",
        "lessee_name": "맨 위 '임대인( )과 임차인( )' 문장에서 임차인 이름이 뭐야? 이름만 답해.",
        "deposit_amount": "제1조 표의 '보증금' 칸에 적힌 ₩ 뒤 숫자 금액이 얼마야? 숫자와 '원'만 답해.",
        "monthly_rent": "제1조 표의 '차임(월세)' 칸에 적힌 금액이 얼마야? 숫자와 '원'만 답해.",
        "contract_start_date": "제2조에서 임차주택을 임차인에게 인도하는 날짜가 언제야? YYYY-MM-DD 형식으로만 답해.",
        "contract_end_date": "제2조에서 임대차기간이 끝나는 날짜가 언제야? YYYY-MM-DD 형식으로만 답해.",
    },
    "sale": {
        "seller_name": "매도인의 성명이 뭐야? 이름만 답해.",
        "buyer_name": "매수인의 성명이 뭐야? 이름만 답해.",
        "sale_price": "'매매대금' 칸의 ₩ 뒤 숫자 금액이 얼마야? 숫자와 '원'만 답해.",
        "down_payment": "'계약금' 칸에 적힌 금액이 얼마야? 숫자와 '원'만 답해.",
        "balance_due_date": "'잔금' 칸에 적힌 지불 날짜가 언제야? YYYY-MM-DD 형식으로만 답해.",
        "handover_date": "제2조에 적힌 부동산 인도일이 언제야? YYYY-MM-DD 형식으로만 답해.",
    },
}
PRE = ("이것은 문서 이미지에 인쇄·기재된 글자를 그대로 옮겨 적는 작업입니다. "
       "설명이나 문장 없이 값만 한 줄로 답하세요. 문서에 없거나 읽을 수 없으면 '없음'이라고만 답하세요.\n")
V2Q = {  # 필드 의미만 분명히(위치 힌트 없음)
    "registry": {
        "property_address": "이 등기사항증명서의 부동산 소재지 주소를 적으세요. '[집합건물]' 같은 표기는 빼고 주소만 적으세요.",
        "owner_name": "현재(가장 최근에 등기된) 소유자의 이름만 적으세요. 이전 소유자나 주소는 제외합니다.",
        "ownership_change_date": "현재 소유자의 소유권이 접수된 날짜를 YYYY-MM-DD 형식으로 적으세요. 등기원인(매매 등)의 날짜가 아니라 접수일입니다.",
        "seizure_flag": "압류 또는 가압류 등기가 하나라도 있으면 '있음', 없으면 '없음'만 적으세요.",
        "mortgage_amount": "근저당권의 채권최고액을 쉼표 포함 숫자로만 적으세요(예: 120,000,000). 근저당 기록이 없으면 '없음'.",
        "mortgage_holder": "근저당권자(은행·회사) 이름만 적으세요. 채무자 이름이 아닙니다. 근저당 기록이 없으면 '없음'.",
    },
    "lease": {
        "lessor_name": "이 임대차계약서의 임대인 이름만 적으세요. 임차인 이름이 아닙니다.",
        "lessee_name": "이 임대차계약서의 임차인 이름만 적으세요. 임대인 이름이 아닙니다.",
        "deposit_amount": "보증금 금액을 쉼표 포함 숫자로만 적으세요(예: 120,000,000). 월세가 아닙니다.",
        "monthly_rent": "차임(월세) 금액을 쉼표 포함 숫자로만 적으세요(예: 1,200,000). 보증금이 아닙니다.",
        "contract_start_date": "임차주택을 임차인에게 인도하는 날짜(임대차기간의 시작)를 YYYY-MM-DD 형식으로 적으세요.",
        "contract_end_date": "임대차기간이 끝나는 날짜를 YYYY-MM-DD 형식으로 적으세요.",
    },
    "sale": {
        "seller_name": "이 매매계약서의 매도인 이름만 적으세요. 매수인 이름이 아닙니다.",
        "buyer_name": "이 매매계약서의 매수인 이름만 적으세요. 매도인 이름이 아닙니다.",
        "sale_price": "매매대금 금액을 쉼표 포함 숫자로만 적으세요(예: 450,000,000). 계약금이 아닙니다.",
        "down_payment": "계약금 금액을 쉼표 포함 숫자로만 적으세요(예: 45,000,000). 매매대금 전체가 아닙니다.",
        "balance_due_date": "잔금을 지불하는 날짜를 YYYY-MM-DD 형식으로 적으세요.",
        "handover_date": "부동산 인도일을 YYYY-MM-DD 형식으로 적으세요.",
    },
}
# 추가 문서 2종(2026-10-24): 집합건축물대장(전유부) · 상가건물 임대차 표준계약서
V2Q["ledger"] = {
    "bldg_address": "이 건축물대장의 대지위치를 적으세요. 지번과 도로명주소는 빼고 대지위치 칸의 값만 적으세요.",
    "bldg_owner": ("소유자현황 표에 적힌 소유자를 모두 적으세요. 한 명이면 성명(명칭)만, 여러 명(공유)이면 "
                   "'성명(지분), 성명(지분)' 형식으로 적으세요(예: 홍길동(1/2), 김민지(1/2)). 주소·주민등록번호는 빼세요."),
    "bldg_owner_date": "소유자현황 표의 변동일자를 YYYY-MM-DD 형식으로 적으세요. 여러 명이면 가장 최근 날짜입니다. 발급일·가격 기준일이 아닙니다.",
    "bldg_main_use": "전유부분 중 '주' 구분 행의 용도만 적으세요(예: 아파트). 공용부분의 용도가 아닙니다.",
    "bldg_area": "전유부분 중 '주' 구분 행의 면적(㎡)을 숫자만 적으세요(예: 84.97). 공용부분 면적이 아닙니다.",
    "bldg_violation": "이 건축물대장에 '위반건축물' 표시가 있으면 '있음', 없으면 '없음'만 적으세요.",
}
V2Q["store_lease"] = {
    "st_lessor": "이 상가건물 임대차계약서의 임대인 이름만 적으세요. 임차인 이름이 아닙니다.",
    "st_lessee": "이 상가건물 임대차계약서의 임차인 이름만 적으세요. 임대인 이름이 아닙니다.",
    "st_deposit": "보증금 금액을 쉼표 포함 숫자로만 적으세요(예: 50,000,000). 환산보증금·계약금이 아닙니다.",
    "st_rent": "차임(월세) 금액을 쉼표 포함 숫자로만 적으세요(예: 2,500,000). 관리비·보증금이 아닙니다.",
    "st_mgmt_fee": "관리비 금액을 쉼표 포함 숫자로만 적으세요(예: 300,000). 차임(월세)이 아닙니다.",
    "st_start": "임차 상가건물을 임차인에게 인도하는 날짜(임대차기간의 시작)를 YYYY-MM-DD 형식으로 적으세요.",
    "st_end": "임대차기간이 끝나는 날짜를 YYYY-MM-DD 형식으로 적으세요.",
}
V2Q["cert"] = {
    "cert_kind": "이 문서가 자격증(합격증·인정서 포함), 수료증, 성적증명서(점수 보고서) 중 무엇인지 그 이름 하나만 적으세요. 셋 다 아니면 '기타'.",
    "cert_holder": "이 증명서를 받은 사람(취득자·수험자·수료자)의 이름을 문서에 적힌 글자 그대로 적으세요. 번역하거나 로마자로 바꾸지 마세요. 발급기관장·서명자 이름이 아닙니다.",
    "cert_title": "자격·시험·과정의 이름을 문서에 적힌 그대로 적으세요(예: 정보처리기사, TOEIC). 문서 제목('자격증', 'Certificate')만 적지 마세요.",
    "cert_grade": "이 증명서의 등급 또는 점수 값 하나만 짧게 적으세요(예: 1급, 850, 6.5). 다른 글자는 적지 마세요. 등급·점수가 없으면 '없음'.",
    "cert_issuer": "이 증명서를 발급한 기관 이름을 문서에 적힌 그대로 적으세요.",
    "cert_date": "자격을 취득(합격·수료)한 날짜를 YYYY-MM-DD 형식으로 적으세요. 성적표는 시험일입니다. 생년월일·발급일이 아닙니다(그 날짜가 없을 때만 발급일).",
    "cert_no": "증명서 번호(자격번호·인정번호·등록번호)를 문서에 적힌 그대로 적으세요. 없으면 '없음'.",
}
HINT = {
    "bldg_address": "(위쪽 표의 '대지위치' 칸)",
    "bldg_owner": "('소유자현황' 표의 '성명(명칭)' 칸과 '소유권 지분' 칸)",
    "bldg_owner_date": "('소유자현황' 표의 '변동일자' 칸)",
    "bldg_main_use": "('전유부분' 표의 '주' 행 '용도' 칸)",
    "bldg_area": "('전유부분' 표의 '주' 행 '면적(㎡)' 칸)",
    "bldg_violation": "(문서 오른쪽 위 표시)",
    "st_lessor": "(맨 위 '임대인( )과 임차인( )' 문장의 '임대인(' 바로 뒤 괄호 안)",
    "st_lessee": "(맨 위 '임대인( )과 임차인( )' 문장의 '임차인(' 바로 뒤 괄호 안)",
    "st_deposit": "(제1조 표의 '보증금' 줄, '원정(₩' 뒤)",
    "st_rent": "(제1조 표의 '차임(월세)' 줄, '원정(₩' 뒤)",
    "st_mgmt_fee": "(제1조 표의 '관리비' 줄, '원정(₩' 뒤)",
    "st_start": "(제2조 '…년 …월 …일까지 임차인에게 인도하고' 부분)",
    "st_end": "(제2조 '임대차기간은 인도일로부터 …년 …월 …일까지로 한다' 부분)",
    "property_address": "(문서 맨 위 '[집합건물]' 뒤에 적힌 줄)",
    "owner_name": "(갑구 표의 가장 아래 행, '권리자 및 기타사항' 칸에서 '소유자' 다음에 적힌 이름)",
    "ownership_change_date": "(갑구 표의 가장 아래 소유권 행, '접수' 칸 첫 줄)",
    "seizure_flag": "(갑구 표의 '등기목적' 칸에 '압류' 또는 '가압류'가 적힌 행)",
    "mortgage_amount": "(을구 표 '권리자 및 기타사항' 칸의 '채권최고액 금' 뒤)",
    "mortgage_holder": "(을구 표의 '근저당권자' 뒤)",
    "lessor_name": "(맨 위 '임대인( )과 임차인( )' 문장의 '임대인(' 바로 뒤 괄호 안)",
    "lessee_name": "(맨 위 '임대인( )과 임차인( )' 문장의 '임차인(' 바로 뒤 괄호 안)",
    "deposit_amount": "(제1조 표의 '보증금' 줄, '원정(₩' 뒤)",
    "monthly_rent": "(제1조 표의 '차임(월세)' 줄, '금' 뒤)",
    "contract_start_date": "(제2조 첫 문장 '…년 …월 …일까지 임차인에게 인도하고' 부분)",
    "contract_end_date": "(제2조 둘째 줄 '임대차기간은 인도일로부터 …년 …월 …일까지로 한다' 부분)",
    "seller_name": "(아래쪽 서명란 '매도인' 블록의 '성명' 칸)",
    "buyer_name": "(아래쪽 서명란 '매수인' 블록의 '성명' 칸)",
    "sale_price": "('매매대금' 줄, '원정(₩' 뒤)",
    "down_payment": "('계약금' 줄에 숫자로 적힌 금액)",
    "balance_due_date": "('잔금' 줄의 '…년 …월 …일에 지불한다' 부분)",
    "handover_date": "(제2조 '위 부동산의 인도일은' 뒤)",
}
V2 = {t: {k: PRE + q for k, q in d.items()} for t, d in V2Q.items()}
V3 = {t: {k: q + " " + HINT.get(k, "") for k, q in d.items()} for t, d in V2.items()}   # 증명서는 양식이 제각각이라 위치 힌트 없음
PROMPTS = {"v1": V1, "v2": V2, "v3": V3}

OCR_NOTE = ("아래는 이 이미지를 OCR로 읽은 글자입니다(위에서 아래 순서, 한 줄에 한 번호). "
            "OCR은 글자가 틀리거나 빠질 수 있으니 이미지와 함께 보고 판단하세요. 같은 줄에 있는 라벨과 값은 서로 짝입니다.\n")


def with_ocr(prompt, lines):
    """질문 앞에 페이지 OCR 줄 목록을 붙인다."""
    body = "\n".join(f"{i + 1}: {L['text']}" for i, L in enumerate(lines))
    return f"{OCR_NOTE}[OCR 시작]\n{body}\n[OCR 끝]\n\n{prompt}"


# ───────── 모델 ─────────
def load(name, dtype):
    import torch
    from transformers import AutoModelForImageTextToText, AutoProcessor
    # 모델 종류(Qwen2.5-VL, Qwen3-VL 등)에 맞는 클래스를 transformers가 고른다
    model = AutoModelForImageTextToText.from_pretrained(name, dtype=getattr(torch, dtype), device_map="auto")
    return model, AutoProcessor.from_pretrained(name)


def ask(model, proc, img, prompt, max_new=64):
    """-> (답변 문자열, 모델이 실제로 본 이미지 크기(w, h)). bbox 좌표는 이 크기 기준이다."""
    import torch
    msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt}]}]
    text = proc.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inputs = proc(text=[text], images=[img], return_tensors="pt").to(model.device)
    grid = inputs["image_grid_thw"][0]
    patch = getattr(proc.image_processor, "patch_size", 14)   # Qwen2.5-VL 14px, Qwen3-VL 16px
    seen = (int(grid[2]) * patch, int(grid[1]) * patch)       # 패치 크기 × 개수 = 리사이즈된 크기
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new, do_sample=False)
    return proc.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0].strip(), seen


def garbled(ans):
    return ans.count("!") >= max(8, len(ans) // 2)
