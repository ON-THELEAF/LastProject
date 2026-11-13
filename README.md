# BridgeDocs (가칭) — 부동산 문서 IDP 졸업작품

부동산 문서와 자격증·수료증·성적증명서(한국어·영어)(PDF·이미지·docx)에서 필요한 값을 읽고, AI가 잘못 읽었을 수 있는 칸을 골라 사람이 확인한 뒤, 엑셀·CSV·txt로 내보낸다. 반대로 데이터를 넣으면 문서를 만든다. 인터넷 없이 Windows GPU PC(RTX 3060 12GB 이상)에서 도는 **윈도우 프로그램**이다. 화면은 웹(React)으로 만들어 이 PC 안(127.0.0.1)에서만 열고 Edge 앱 창으로 띄운다.

> 엔진(DesktopService) 약속은 [`SERVER/INTERFACE.md`](SERVER/INTERFACE.md)가 기준이다.

## 실행

- **팀원 PC 처음 한 번:** Git·Python 3.11·Node.js를 깔고 `git clone https://github.com/ON-THELEAF/LastProject.git` → `BridgeDocs 설치.bat`(패키지 설치·화면 빌드·AI 모델 약 9GB 받기, 모델은 git에 안 올라감)
- **그다음부터:** `BridgeDocs 실행.bat`만 누른다. 켤 때마다 GitHub에서 최신 코드를 받고(`git pull`), 바뀐 게 있으면 패키지·화면을 다시 빌드한 뒤 실행한다. 내 수정을 팀원에게 주려면 커밋하고 `git push`.
- 소스로(GPU PC): `BridgeDocs 실행.bat` 또는 `python SERVER\web_app.py` (먼저 `Front` 폴더에서 `npm install` → `npm run build`로 `Front/dist/`를 만든다. 화면을 고치면 다시 `npm run build`)
- 엔진만 명령줄로: `python Fuc/bridgedocs.py run 문서.pdf …` / `make 데이터.csv` — [`Fuc/README.md`](Fuc/README.md)

## 시스템 구조

```
화면 Front/  →  서버 SERVER/(web_app·web_api·service)  →  기능 Fuc/(bridgedocs.py가 입구)  →  저장 DB/ · 모델 models/
```

| 단계 | 하는 일 |
|---|---|
| **1. 문서 입력** | PDF·이미지·docx 받기 → 쪽 이미지로 바꾸기 → 스캔본 기울기 보정 → 문서 종류 판별(처음 보는 종류는 새 종류) |
| **2. 문서 분석** | **PDF 안 글자**(믿을 수 없는 쪽만 OCR) · **OCR**(PaddleOCR 한국어 모델) · **docx 글자** 중 자동 선택. 읽은 줄을 힌트로 AI(Qwen3-VL-4B)에게 칸마다 질문 |
| **3. 환각검증·사람 확인** | 규칙 검사·두 번째 판독 → 자동 확정 / 확인 필요. 문서 읽기 탭에서 칸을 누르면 문서 위에 상자(노랑 = 확정, 빨강 = 확인 필요) |
| **4. 내보내기** | 엑셀·CSV·txt 저장(이어 붙이기 가능). 문서마다 결과 JSON은 읽을 때 바로 저장 |

**문서 만들기(반대 방향, AI 안 씀):** 데이터(CSV·엑셀·txt·JSON) → 값 확인 → 사용자가 넣은 워드 양식의 빈칸(`{{…}}`)에 값을 순서대로 넣은 문서(표 한 줄 = 문서 하나). 고정 기본 양식은 없음.

## 폴더

| 폴더/파일 | 내용 |
|---|---|
| `Front/` | 화면. `src/`(피그마 메이크 코드 기반): `App.tsx`(홈·위쪽 메뉴), `reader.tsx`(문서 읽기·결과 확인·내보내기), `make.tsx`(문서 만들기 1~3단계), `documents.tsx`(문서 관리), `ui.tsx`(공통 모양), `api.ts`(엔진 부르기). `dist/`는 `npm run build` 결과(저장소에 안 올림) |
| `SERVER/` | 서버: `web_app.py`(이 PC 안 서버·Edge 앱 창), `web_api.py`(화면 버튼 ↔ DesktopService), `service.py`(엔진의 유일한 입구), `worker.py`(읽기 작업 스레드), `offline.py`(인터넷 차단·모델 위치·설정.json), `INTERFACE.md`(약속), `requirements.txt` |
| `Fuc/` | 기능: `bridgedocs.py`(입구·명령줄) 아래 기능별 폴더 — `path_a_ocr`(A경로 OCR), `pdf_text`(PDF 안 글자), `docx_read`(docx 글자), `path_b_vlm`(B경로 AI 질문), `hallucination_check`(환각검증·재판독), `read_pipeline`(문서 읽기 전체 흐름·종류 판별·기울기 보정), `view`(결과 보기 HTML), `export`(판정·내보내기), `make_doc`(문서 만들기), `scoring`(정답 비교) |
| `DB/` | 저장·불러오기: `storage.py`(새 문서 종류 목록 JSON, 워드 양식 폴더, 결과 JSON 다시 저장) |
| `models/` | AI 모델(약 9GB, 저장소에 안 올림) |

## 설치할 때 정하는 폴더

프로그램 옆에 `설정.json`을 두면 워드 양식 폴더와 저장 폴더(읽기 결과 JSON·내보내기)를 바꿀 수 있어요. 없으면 양식은 프로그램 옆 `양식\`, 저장은 `문서\BridgeDocs 결과`예요.

```json
{"양식 폴더": "D:/사무소/양식", "저장 폴더": "D:/사무소/결과"}
```

- 필요 사양: 그래픽카드 메모리(VRAM) 12GB 이상.
