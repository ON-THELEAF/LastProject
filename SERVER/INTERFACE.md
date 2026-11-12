# 화면 ↔ 기능 약속 (INTERFACE)

화면(`web_api.py`)은 `DesktopService`만 부릅니다. `bridgedocs.py`를 직접 부르지 않습니다.
오류는 모두 **화면에 그대로 보여 줄 수 있는 한국어 문구의 `ValueError`** 로 옵니다.

## 시작

```python
import sys; sys.path[:0] = [".", "SERVER", "Fuc"]   # 프로젝트 맨 위에서
import offline
models = offline.setup_offline(offline.app_base())   # torch·paddle을 불러오기 전에
from service import DesktopService
svc = DesktopService(models)
```

## 1. 문서 읽기

| 함수 | 하는 일 |
|---|---|
| `status()` | `{"models_ok", "missing", "message"}`. 모델이 없으면 읽기는 못 하고 만들기는 됩니다. |
| `supported_suffixes()` | `[".pdf", ".png", ".jpg", ".jpeg", ".docx"]` |
| `preview_groups(files)` | 읽기 전에 **사용자에게 보여 주고 확인받을 묶음**. `{"groups": [{"name", "files"}], "rejected": [{"file", "reason"}]}`. `X_p1.png, X_p2.png`는 한 문서로 묶입니다. |
| `start_read(files, out_dir, deskew, want=None, group=True)` | 작업(`ReadJob`)을 돌려줍니다. 돌려받은 직후 신호를 연결하세요. `want`: 사용자가 고른 칸 이름들(주면 문서 종류와 상관없이 그 칸만 읽음, 비면 AI가 정함). `group=False`: 사용자가 쪽 묶음을 거절 → 이미지마다 따로 읽음. |
| `default_out_dir()` | 설치할 때 정한 저장 폴더(프로그램 옆 `설정.json`의 `"저장 폴더"`, 없으면 `문서\BridgeDocs 결과`). 읽기 결과 JSON·내보내기의 기본 자리 |
| `save_doc(doc)` | 확인 중인 문서(확정·수정 포함)를 그 문서의 `json_path`에 다시 저장. 다른 문서로 넘어갈 때 부르세요(자동 저장) |
| `busy()` / `wait_all()` | 작업 중인지 / 창 닫을 때 모두 취소하고 기다리기 |

`ReadJob`:
- 신호
  - `progress(str)`: 진행 문구
  - `doc_done(dict)`: 읽은 문서 하나(아래 "읽기 결과")
  - `doc_failed(이름, 사유)`
  - `finished(성공 수, 실패 수)`
  - `queued(int)`: 대기 순번이 바뀔 때(앞 작업이 끝나 한 칸 당겨짐, 0이면 지금 시작). 화면 객체의 메서드에 연결하세요(메인 스레드에서 받음).
- `job.queue_position`: 0이면 바로 시작해요. n이면 **앞 작업 n개가 끝난 뒤 시작**해요(대기열). 읽는 중에 또 넣어도 거절하지 않아요.
- `job.cancel()`: **지금 읽는 문서도 바로 멈춰요**(그 문서 결과는 버림). 이미 끝난 문서의 JSON은 남아요. 대기 중인 작업이 있으면 그것까지 취소할지 사용자에게 물어보세요(작업마다 따로 `cancel()`).
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
  - 확정한 칸 목록은 나중에 고치거나(`save_custom_type`을 같은 이름으로 다시 부르면 덮어씀) 지울 수 있어요(`delete_custom_type(name)`).
  - 새 문서 종류는 **모든 쪽**을 읽어요. 앞 쪽에서 찾은 칸은 뒤 쪽에서 다시 묻지 않고, 칸마다 찾은 쪽이 `page`에 들어가요.
- 두 번째 판독이 실패하면(오류) 그 칸은 확인 필요(`reason`: "두 번째 판독 실패 — 확인하세요")로 와요.

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
- **문서 하나가 한 화면**이에요. 확인 버튼을 누르면 다음 확인 필요 칸으로, 그 문서에 없으면 다음 문서로 자동으로 넘어가요. 다 끝나면 위쪽에 완료 안내를 보이고 그 자리에 머물러요.
- 원본 값은 고치지 않고 **비교 값 칸만 고쳐요**(2026-11-10 피그마). [비교 값 적용] → 비교 값이 원본과 같으면 `confirmed`(사용자 확인), 다르면 `edited`(사용자 수정, 처음 읽은 값은 `read_value`에 남음). [되돌리기] → `undo`.
- 화면에는 취소·저장 폴더·읽을 칸 고르기·문서 종류 관리·TXT 구분 기호 버튼이 없어요(2026-11-10 사용자 결정). 서비스의 `want`·`sep` 등은 그대로 있어요.
- [이 칸 확정]은 칸을 골랐을 때만 보여요(원본 값 그대로 `confirmed`). TXT는 세로줄 `|`로 구분해 내보내요(피그마).
- 새 문서(`new_type`)는 칸 이름을 표에서 바로 고쳐요. 내보낼 때 "새 문서 양식을 등록할까요?"를 묻고, 등록하면 `save_custom_type(종류 이름, 고친 칸 이름들)`을 불러요.
- "한 번에 확정"은 현재 문서의 확인 필요 칸에만 적용해요.
- 쪽 이미지를 못 불러온 문서는 목록에 ✕ 표시를 하고 확인 순서에서 건너뛰어요(오른쪽 표는 그대로 써요). 문서 목록은 숨길 수 있어요.
- "한 번에 확정" 버튼을 두세요(`decide_all`).
- 작업 스레드 신호로만 화면을 갱신하세요(창이 멈추지 않게).

## 3. 내보내기

`export(docs, kinds, path_stem, append=False, sep="\t", force=False, only=None)`:
- `kinds`: `"xlsx"`, `"csv"`, `"txt"` 중 하나 이상. `sep`: TXT 구분 기호(탭·쉼표 `,`·세미콜론 `;`·세로줄 `|`, 사용자가 고름).
- `only`: 이 칸 이름들만 내보내요. 문서 두 건 이상을 한 파일로 묶을 때 사용자가 고른 칸이에요(목록은 `field_labels(docs)`).
- 파일은 임시 파일에 먼저 쓰고 성공하면 원래 파일과 바꿔요(이어 붙이다 실패해도 원래 파일은 그대로).
- **저장할 때마다 사용자에게 물어보세요.** 새 파일로 할지, 기존 파일에 이어 붙일지(`append=True`, 파일 고르기 창)요. 이어 붙이기는 같은 문서 종류이고 이 프로그램이 내보낸 파일일 때만 돼요. 아니면 `ValueError`를 내요.
- 이어 붙이기에서 여러 형식을 고르면, 형식마다 같은 이름의 기존 파일이 모두 있어야 해요(없으면 "이어 붙일 파일이 없어요"). 내보낸 파일 내용은 `read_rows(path)`로 읽어요.
- **JSON은 내보내지 않아요.** JSON을 원하는 사용자에게는 `json_folders(docs)`가 돌려주는 폴더를 알려 주세요. `"json"`을 넘기면 그 폴더를 적은 `ValueError`가 와요.
- 확인 필요 칸이 남아 있으면 `ValueError("남은 확인 N칸 …")`를 내요. 화면은 먼저 "남은 확인 N칸이 있어요. 그래도 내보낼까요?"라고 묻고, 사용자가 승인하면 `force=True`로 불러요.

## 4. 문서 만들기 (모델 필요 없음)

| 함수 | 하는 일 |
|---|---|
| `list_templates()` | 고를 양식: 사용자가 넣은 워드 양식만(고정 기본 양식 없음). 하나도 없으면 `[]` |
| `add_template(path)` / `remove_template(id)` | 워드 양식 추가(양식 폴더로 복사) / 사용자 양식 지우기 |
| `template_slots(template)` | 양식 안 빈칸(`{{…}}`) 이름들, 문서에 나오는 순서대로 |
| `preview_data(data_path, n=8)` | 데이터 파일 앞부분 n줄. 데이터를 고르면 이걸 창에 보여 주고 "첫 줄은 제목"을 그 창에서 정해요 |
| `prepare_make(data_path, template, header=False)` | 데이터(JSON·CSV·엑셀·txt) → 만들 문서 목록 `[{name("문서1"…), values{"1. 빈칸 이름": 값}(순서대로), problems([]), notes, template_path}]`. 양식(.docx)이 없거나 빈칸이 없으면 `ValueError` |
| `preview_make(doc, values)` | **고칠 곳 목록**: `{"pages": [], "marks": [{key, label, reason, page: None, box: None}]}`. 비어 있는 빈칸마다 마크가 하나씩 있어요. |
| `made_paths(docs, posted, out_dir, name="문서")` | 만들 파일 경로들(`{name}{줄 번호}.docx`). 같은 이름 파일이 있으면 덮어쓸지 사용자에게 물어보세요 |
| `submit_make(docs, posted, out_dir, name="문서")` | 만들기(파일 이름은 사용자가 정한 `name` 뒤에 줄 번호). `posted = [{"name", "make": bool, "values": {칸 키: 값}}]` → `{"problems": {}, "results": [{name, path, message}]}` |
| `convert_made(paths, kind, names=None)` | 만든 .docx를 같은 폴더에 `"pdf"` 또는 쪽마다 `"png"` 그림으로도 저장 → 새 파일 목록. `names`: 저장 전에 사용자가 고친 파일 이름들. PNG는 `이름.png`, 여러 쪽이면 `이름_1.png`…. PDF 변환은 이 PC의 Microsoft Word를 써요(없으면 `ValueError`) |


- **순서대로 채우기(2026-11-06 결정):** 표의 한 줄이 문서 하나예요. 값을 왼쪽부터 양식의 빈칸에 **문서에 나오는 순서대로** 넣어요. 칸 이름·데이터 파일 이름은 보지 않아요(`{{}}`처럼 비워 둬도 돼요).
- 첫 줄이 제목인지는 **사용자가 정해요**(데이터를 고르면 뜨는 창의 "첫 줄은 제목" 체크박스 → `header=True`면 첫 줄을 건너뜀, 기본은 첫 줄부터 값). JSON에는 상관없어요.
- 양식 추가·삭제는 **문서 관리** 화면에서 해요(문서 만들기의 [문서 관리] 버튼). 삭제는 확인 창을 띄워요.
- 값이 모자라면 남은 빈칸은 비워 두고, 남으면 뒤쪽 값은 빼요. 둘 다 `notes`에 적어요. 만든 파일 이름은 기본 `문서1.docx`, `문서2.docx`…이고, 사용자가 앞 이름을 바꿀 수 있어요.
- JSON(읽기 결과)은 문서마다 칸 값들을 순서대로 한 줄로 봐요.
- CSV·txt는 UTF-8과 CP949(한국어 엑셀 기본 저장) 둘 다 읽어요. txt의 구분 기호(탭·쉼표·세미콜론·세로줄)는 자동으로 판단해요.
- 만들다 실패하면 `ValueError`를 내요.
