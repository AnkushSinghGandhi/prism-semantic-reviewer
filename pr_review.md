# Semantic Review — PR #122430: fix(auth): lock the invited email on the invite registration form

`9cca6d24` → `34e8237b`  |  line diff: 3 files changed, 104 insertions(+), 24 deletions(-)

**0 semantic changes** — 

| | change | route |
|--|--------|-------|

---

---
_Blindspot: this diff sees routing/auth/db/external/async at the endpoint lens; it does not see logic inside a function, ordering, concurrency, or off-by-one._