# 화면 ↔ 기능 약속 (INTERFACE)

화면(`frontend/`)은 `DesktopService`(진짜) 또는 `FakeService`(연습용, GPU·모델 없이 동작)만 부릅니다. `bridgedocs.py`를 직접 부르지 않습니다.
오류는 모두 **화면에 그대로 보여 줄 수 있는 한국어 문구의 `ValueError`** 로 옵니다.

## 시작

```python
import sys; sys.path.insert(0, "pipeline")
from desktop import offline
models = offline.setup_offline(offline.app_base())   # torch·paddle을 불러오기 전에
from desktop.service import DesktopService            # 개발 중에는 from desktop.fake import FakeService
svc = DesktopService(models)                           # FakeService(delay=0.3)
```

## 1. 문서 읽기

| 함수 | 하는 일 |
|---|---|
| `status()` | `{"models_ok", "missing", "message"}`. 모델이 없으면 읽기는 못 하고 만들기는 됩니다. |
| `supported_suffixes()` | `[".pdf", ".png", ".jpg", ".jpeg", ".docx"]` |
| `preview_groups(files)` | 읽기 전에 **사용자에게 보여 주고 확인받을 묶음**. `{"groups": [{"name", "files"}], "rejected": [{"file", "reason"}]}`. `X_p1.png, X_p2.png`는 한 문서로 묶입니다. |
| `start_read(files, out_dir, deskew)` | 작업(`ReadJob`)을 돌려줍니다. 돌려받은 직후 신호를 연결하세요. |
| `busy()` / `wait_all()` | 작업 중인지 / 창 닫을 때 모두 취소하고 기다리기 |

`ReadJob`:
- 신호
  - `progress(str)`: 진행 문구
  - `doc_done(dict)`: 읽은 문서 하나(아래 "읽기 결과")
  - `doc_failed(이름, 사유)`
  - `finished(성공 수, 실패 수)`
  - `queued(int)`: 대기 순번이 바뀔 때(앞 작업이 끝나 한 칸 당겨짐, 0이면 지금 시작). 화면 객체의 메서드에 연결하세요(메인 스레드에서 받음).
- `job.queue_position`: 0이면 바로 시작해요. n이면 **앞 작업 n개가 끝난 뒤 시작**해요(대기열). 읽는 중에 또 넣어도 거절하지 않아요.
- `job.cancel()`: **지금 읽는 문서도 바로 멈춰요**(그 문서 결과는 버림). 이미 끝난 문서의 JSON은 남아요.
- 지원하지 않는 파일이나 없는 파일은 그 파일만 `doc_failed(파일 이름, "지원하지 않는 파일 …")`로 돌아와요. 나머지는 읽어요. 모든 파일이 그렇다면 `start_read`가 `ValueError`를 내요.
- 문서마다 결과 JSON을 `out_dir`에 바로 저장해요. 그 경로는 결과의 `json_path`에 있어요.

### 읽기 결과 (doc_done으로 오는 dict)

```json
{"source": ["clean_s01_ledger_p1.png"], "files": ["…절대 경로…"], "route": ["ocr"], "pages": 1,
 "doc_type": "ledger", "doc_name": "건축물대장", "new_type": false, "json_path": "…/clean_s01_ledger.json",
 "summary": {"auto": 5, "review": 1},
 "fields": [
  {"key": "bldg_address", "label": "대지위치", "kind": "text", "value": "인천광역시 연수구 구월동",
   "status": "auto", "reason": "", "candidates": [], "compare": "", "page": 1, "line_box": [109, 269, 1034, 302]},
  {"key": "lessee_name", "label": "임차인", "kind": "name", "value": "이영희",
   "status": "review", "reason": "두 번 읽은 값이 다름(이영희 / 이영회)", "candidates": ["이영희", "이영회"], "compare": "이영회",
   "page": 1, "line_box": null}]}
```

- `status`: `auto`(자동 확정) · `review`(확인 필요) · `confirmed`(사용자 확인) · `edited`(사용자 수정). 한국어 이름은 `labels()["status"]`에 있어요.
- `compare`: **확인 필요 칸에서 사용자에게 보여 줄 비교 값 하나**예요. 칸마다 두 번 읽은 값이 다르면 다른 쪽 값이 들어가요. 비교할 값이 없으면 `""`예요.
- `line_box`: 쪽 이미지 원래 크기 기준 상자 `[x0, y0, x1, y1]`예요(`page_images(doc)`의 `w`·`h` 기준).
- **문서 종류는 AI가 정해요.**
  - 알려진 6종이면 `doc_type`이 그 키예요.
  - 처음 보는 문서면 `doc_type: "custom"`, `doc_name: "위임장"` 같은 이름이 와요. 칸은 AI가 골라요.
  - `new_type: true`면 모든 칸이 확인 필요예요. 사용자가 **칸 이름 목록을 고치고 확정하면** `save_custom_type(doc_name, [칸 이름…])`을 부르세요. 다음에 같은 종류가 오면 그 칸들을 읽어요(`new_type: false`).
  - 저장된 종류 목록은 `custom_types()` → `{종류 이름: [칸 이름…]}`. 저장 파일이 깨져 있으면 `save_custom_type`이 덮어쓰지 않고 `ValueError`를 내요.
- 중국어·일본어 증명서는 모든 칸이 확인 필요로 와요(처음 읽은 값을 사용자가 확인).

## 2. 확인 화면(판별)

| 함수 | 하는 일 |
|---|---|
| `page_images(doc)` | 쪽 이미지 `[{"src": base64 JPEG, "w", "h"}]` |
| `parse_value(kind, s)` | 직접 입력한 값 검사 → `(정리된 값, 오류 문구)` |
| `decide(field, value, status)` | 칸 하나 판정한 새 dict(원본 그대로) |
| `decide_all(doc)` | **"한 번에 확정" 버튼**: 그 문서의 확인 필요 칸을 보이는 값 그대로 모두 확정한 새 문서 |
| `undo(field)` / `review_left(docs)` | 되돌리기 / 남은 확인 칸 수 |

화면 규칙(2026-10-28 사용자 결정):
- 확인 필요 칸에는 **비교 값 하나(`compare`)** 만 보여 주세요.
- **문서 하나가 한 화면**이에요. 확인 버튼을 누르면 다음 쪽이나 다음 문서로 자동으로 넘어가요.
- "한 번에 확정" 버튼을 두세요(`decide_all`).
- 작업 스레드 신호로만 화면을 갱신하세요(창이 멈추지 않게).

## 3. 내보내기

`export(docs, kinds, path_stem, append=False)`:
- `kinds`: `"xlsx"`, `"csv"`, `"txt"`(탭으로 구분) 중 하나 이상.
- **저장할 때마다 사용자에게 물어보세요.** 새 파일로 할지, 기존 파일에 이어 붙일지(`append=True`, 파일 고르기 창)요. 이어 붙이기는 같은 문서 종류이고 이 프로그램이 내보낸 파일일 때만 돼요. 아니면 `ValueError`를 내요.
- 이어 붙이기에서 여러 형식을 고르면, 형식마다 같은 이름의 기존 파일이 모두 있어야 해요(없으면 "이어 붙일 파일이 없어요"). 내보낸 파일 내용은 `read_rows(path)`로 읽어요.
- **JSON은 내보내지 않아요.** JSON을 원하는 사용자에게는 `json_folders(docs)`가 돌려주는 폴더를 알려 주세요. `"json"`을 넘기면 그 폴더를 적은 `ValueError`가 와요.
- 확인 필요 칸이 남아 있으면 `ValueError("남은 확인 N칸 …")`를 내요.

## 4. 문서 만들기 (모델 필요 없음)

| 함수 | 하는 일 |
|---|---|
| `list_templates()` | 고를 양식: 기본 양식(`builtin:<종류>`, 증명서는 없음) + 사용자가 넣은 워드 양식 |
| `add_template(path)` / `remove_template(id)` | 워드 양식 추가(양식 폴더로 복사) / 사용자 양식 지우기 |
| `template_keys(doc_type)` | 워드 양식에 쓸 수 있는 `{{이름}}` 목록 |
| `prepare_make(data_path, template=None, doc_type=None)` | 데이터(JSON·CSV·엑셀·**txt**) → 만들 문서 목록 `[{name, doc_type, values, filled([]), problems, notes, template_path}]` |
| `preview_make(doc, values)` | **미리보기와 고칠 곳 상자**: `{"pages": [{src,w,h}], "marks": [{key, label, reason, page, box, boxes}]}`. 비었거나 형식이 틀린 칸마다 마크가 하나씩 있어요. 워드 양식은 `pages: []`예요. |
| `submit_make(docs, posted, out_dir)` | 만들기. `posted = [{"name", "make": bool, "values": {칸 키: 값}}]` |

- 데이터에 없는 칸은 **빈칸으로 둬요**(임의 값으로 채우지 않아요. `filled`는 항상 `[]`). 빈칸은 만들기를 막지 않아요.
- 형식이 틀린 칸이 하나라도 있으면 아무 파일도 만들지 않고 `{"problems": {이름: [문구]}, "results": []}`를 돌려줘요. 화면은 `preview_make`의 상자로 고칠 자리를 보여 주세요.
- 값을 확인한 뒤 **한 번에 확정하는 버튼**을 두세요(화면에서 posted를 한꺼번에 만듦).
- CSV·txt는 UTF-8과 CP949(한국어 엑셀 기본 저장) 둘 다 읽어요.
- txt 데이터의 구분 기호(탭·쉼표·세미콜론·세로줄)는 자동으로 판단해요. `칸: 값` 줄 형식도 받아요. 모르는 칸은 빼고 `notes`에 적어요.
- 만들다 실패하면 `ValueError`를 내요.
