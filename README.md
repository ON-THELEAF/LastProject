# BridgeDocs (가칭) — 부동산 문서 IDP 졸업작품

부동산 문서와 자격증·수료증·성적증명서(한국어·영어·일본어·중국어)(PDF·이미지·docx)에서 필요한 값을 읽고, AI가 잘못 읽었을 수 있는 칸을 골라 사람이 확인한 뒤, 엑셀·CSV·txt로 내보낸다. 반대로 데이터를 넣으면 문서를 만든다. 인터넷 없이 Windows GPU PC(RTX 3060 12GB 이상)에서 도는 **윈도우 프로그램**(PySide6)이다.

> 화면↔기능 약속은 [`frontend/INTERFACE.md`](frontend/INTERFACE.md)가 기준이다.

## 실행

- 소스로(GPU PC): `python frontend\app.py`
- 엔진만 명령줄로: `python pipeline/bridgedocs.py run 문서.pdf …` / `make 데이터.csv` — [`pipeline/README.md`](pipeline/README.md)

## 시스템 구조

그림: [`docs/프로그램 구조도 (11.2).png`](<docs/프로그램 구조도 (11.2).png>) · 고치려면 [`docs/프로그램 구조도(11.2).excalidraw`](<docs/프로그램 구조도(11.2).excalidraw>) (VS Code Excalidraw 확장으로 열기)

```
화면 frontend/  →  백엔드 pipeline/desktop/service.py  →  엔진 pipeline/bridgedocs.py  →  모델 models/
```

| 단계 | 하는 일 |
|---|---|
| **1. 문서 입력** | PDF·이미지·docx 받기 → 쪽 이미지로 바꾸기 → 스캔본 기울기 보정 → 문서 종류 판별(처음 보는 종류는 새 종류) |
| **2. 문서 분석** | **PDF 안 글자**(믿을 수 없는 쪽만 OCR) · **OCR**(PaddleOCR, 중·일 문서는 중·일 OCR) · **docx 글자** 중 자동 선택. 읽은 줄을 힌트로 AI(Qwen3-VL-4B)에게 칸마다 질문 |
| **3. 환각검증·사람 확인** | 규칙 검사·두 번째 판독 → 자동 확정 / 확인 필요. 문서 읽기 탭에서 칸을 누르면 문서 위에 상자(노랑 = 확정, 빨강 = 확인 필요) |
| **4. 내보내기** | 엑셀·CSV·txt 저장(이어 붙이기 가능). 문서마다 결과 JSON은 읽을 때 바로 저장 |

**문서 만들기(반대 방향):** 데이터(CSV·엑셀·txt, 보통 문장 txt는 AI가 읽음) → 값 확인 → 사용자가 넣은 워드 양식의 빈칸(`{{…}}`)에 값을 순서대로 넣은 문서(표 한 줄 = 문서 하나). 고정 기본 양식은 없음.

## 폴더

| 폴더/파일 | 내용 |
|---|---|
| `frontend/` | 화면: `app.py`(창·탭), `read_tab.py`(문서 읽기, 임시판), `make_tab.py`(문서 만들기), `INTERFACE.md`(약속) |
| `pipeline/desktop/` | 백엔드: `service.py`(화면이 부르는 유일한 입구), `worker.py`(작업 스레드), `export.py`(검사·내보내기), `offline.py`(인터넷 차단·모델 위치) |
| `pipeline/bridgedocs.py` | 엔진 전체(읽기 1~3단계, 문서 만들기, 실험 도구) |
| `docs/` | 프로그램 구조도 |
| `models/` | AI 모델(약 9GB, 저장소에 안 올림) |
