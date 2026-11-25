"""B경로: VLM(Qwen) 질문 프롬프트·모델 불러오기  (bridgedocs.py에서 나눔)"""
from pathlib import Path


# 주의: fp16으로 돌리면 문서 1장 응답이 통째로 "!!!!"로 깨진 적이 있다(실측). bf16으로 부른다.

HERE = Path(__file__).parent

# 칸마다 묻는 질문은 양식에 있다(DB/기본 양식.json의 q·hint, 사용자 양식은 칸 이름과 종류로 만든다: DB.forms.question)

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
