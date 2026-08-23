# Candidate Invariants — discovered from 3 snapshots

history sampled `ec99cf5bdf` … `3bdba223af` (oldest→newest). Ranked: near-misses first (rule-with-exceptions or latent bugs), then boundaries, then persistent universals.

## ⚠ NEAR-MISS — Authenticated access before any DB write
- **holds:** 17/21 endpoints (80%)  ·  **persistence:** 100% → 100% → 81% across 3 snapshots
- **exceptions — look here (4):**
    - `api/v1/payment/product-details` — open (AllowAny)  (`cnext_backend/apps/college_predictor_payment/views/product_views.py:18`)
    - `api/v1/companions/payment/invoice` — open (AllowAny)  (`cnext_backend/apps/payment/views/invoice_views.py:9`)
    - `api/<int:version>/upload-image` — open (AllowAny)  (`cnext_backend/apps/tools/api/controllers.py:24`)
    - `college-predictor/report-pdf` — open (AllowAny)  (`cnext_backend/apps/college_predictor/views.py:198`)
- **suggested:** severity=critical, scope=all endpoints performing a DB write
- _confirm → freeze as invariant (baseline the exceptions or fix them)_

## ⚠ NEAR-MISS — PII read + egress only on authenticated endpoints
- **holds:** 8/11 endpoints (72%)  ·  **persistence:** 100% → 100% → 72% across 3 snapshots
- **exceptions — look here (3):**
    - `api/v1/payment/last-usage` — open (AllowAny)  (`cnext_backend/apps/college_predictor_payment/views/usage_views.py:99`)
    - `api/v1/companions/payment/invoice` — open (AllowAny)  (`cnext_backend/apps/payment/views/invoice_views.py:9`)
    - `college-predictor/report-pdf` — open (AllowAny)  (`cnext_backend/apps/college_predictor/views.py:198`)
- **suggested:** severity=critical, scope=endpoints reading PII with an egress path
- _confirm → freeze as invariant (baseline the exceptions or fix them)_

## 🔒 BOUNDARY — External/PII egress limited to 1 known destinations
- **destinations (1):** settings.PAYTM_STATUS_URL
- **added at HEAD:** settings.PAYTM_STATUS_URL → a *new* destination in a PR is a boundary change (M5 guards this)
- **suggested:** severity=critical, scope=all outbound calls
- _confirm → freeze as invariant (baseline the exceptions or fix them)_

---
_Discovery only. Historical persistence is the prior that a property is intentional; a snapshot-universal is often structural trivia. Confirm the ones that were on purpose — that confirmed corpus is the compounding asset._