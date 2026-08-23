# Semantic Review — Merge pull request #191 from careers-360/feat/cut-off-compare-main

`ceab6b43` → `bad13a0c`  |  line diff: 16 files changed, 1632 insertions(+), 1164 deletions(-)

**1 semantic changes** — 🟡 1

| | change | route |
|--|--------|-------|
| 🟡 | CHANGED | `api/<int:version>/compare/exam-cutoff-graph-comparison` |

---

### 🟡 CHANGED — `api/<int:version>/compare/exam-cutoff-graph-comparison`
- **why:** new DB write: <instance>:write
- **flow:** api/<int:version>/compare/exam-cutoff-graph-comparison → ExamCutGraphoffView → {<instance>:write, CollegeCourse:read, Exam:read}
- **investigate:**
    - `cnext_backend/apps/college_compare/api/controllers.py:1553` — handler `ExamCutGraphoffView` (GET)
    - `cnext_backend/apps/college_compare/helper/cut_off_helper.py:1234` — <instance>:write (via call)
- **unknown:** db ⚠: <instance>:write, CollegeCourse:read, Exam:read — write hidden in manager/service

---
_Blindspot: this diff sees routing/auth/db/external/async at the endpoint lens; it does not see logic inside a function, ordering, concurrency, or off-by-one._