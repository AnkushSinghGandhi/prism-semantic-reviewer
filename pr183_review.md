# Semantic Review — Merge pull request #183 from careers-360/common_companion_jun

`37485e83` → `1e8a628a`  |  line diff: 94 files changed, 17841 insertions(+), 35 deletions(-)

## 🛡 Invariants
**3 invariant alert(s)** — a confirmed property of this codebase is affected by this PR:

- **🔴 NEW VIOLATION — Authenticated access before any DB write**
    - 1 endpoint(s) introduced/changed by this PR break this invariant
    - `api/v1/companions/payment/invoice — open (AllowAny) @ cnext_backend/apps/payment/views/invoice_views.py:9`
- **🔴 NEW VIOLATION — PII read + egress only on authenticated endpoints**
    - 1 endpoint(s) introduced/changed by this PR break this invariant
    - `api/v1/companions/payment/invoice — open (AllowAny) @ cnext_backend/apps/payment/views/invoice_views.py:9`
- **🔴 WEAKENING — External/PII egress limited to approved destinations**
    - egress allowlist would expand — new destination(s) not among the 0 approved: settings.PAYTM_STATUS_URL
- **🟡 pre-existing — Authenticated access before any DB write**
    - 3 pre-existing violation(s) (not this PR)
- **🟡 pre-existing — PII read + egress only on authenticated endpoints**
    - 2 pre-existing violation(s) (not this PR)

**31 semantic changes** — 🔴 3  🟠 4  🟡 4  🟢 20

| | change | route |
|--|--------|-------|
| 🔴 | NEW ENDPOINT | `api/v2/companions/payment/create-order` |
| 🔴 | NEW ENDPOINT | `api/v1/companions/payment/order-status` |
| 🔴 | NEW ENDPOINT | `api/v1/companions/payment/invoice` |
| 🟠 | NEW ENDPOINT | `api/v1/companions/payment/product-details` |
| 🟠 | NEW ENDPOINT | `api/v1/companions/payment/validate-coupon` |
| 🟠 | NEW ENDPOINT | `api/v1/companions/payment/callback/<int:order_id>` |
| 🟠 | NEW ENDPOINT | `api/v1/companions/order-shipping-address` |
| 🟡 | NEW ENDPOINT | `api/token/` |
| 🟡 | NEW ENDPOINT | `api/token/refresh/` |
| 🟡 | NEW ENDPOINT | `api/<int:version>/announcements/read` |
| 🟡 | CHANGED | `api/<int:version>/college-predictor/cutoff-index/rebuild` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/college-predictor/seat-allotment/tuples` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/college-predictor/seat-allotment/search` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/college-predictor/seat-allotment/filter-options` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/college-predictor/seat-allotment/dropdowns` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/college-predictor/seat-allotment/college-seat-data` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/college-predictor/seat-allotment/cache-flush` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/college-predictor/seat-allotment/cutoff-data-sync` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/announcements/dashboard` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/announcements/list` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/announcements/filters` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/dashboard/summary` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/dashboard/prediction-stats` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/companion_landing/home-page` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/companion_landing/application-open-listing` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/companion_landing/application-open-filters` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/companion_landing/application-open-search` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/companion_landing/companion-purchase-check` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/companion_landing/companion-recommended-tools-cp` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/companion_landing/companion-recommended-tools-rp` |
| 🟢 | NEW ENDPOINT | `api/<int:version>/companion_landing/registration-prefill` |

---

### 🔴 NEW ENDPOINT — `api/v2/companions/payment/create-order`
- **why:** new endpoint reads PII and has an egress path
- auth: permission_classes=['IsAuthenticated']
- **flow:** api/v2/companions/payment/create-order → CompanionCreateOrder → {<instance>:write, CPProductCampaign:read, CareersProductCouponCode:read, CareersProductCouponPack:read, Order:read, Order:write, OrderedItems:write, ProductEntities:read, UrlAlias:read, User:read} → ext: requests.post -> ?
- **investigate:**
    - `cnext_backend/apps/payment/views/payment_views.py:56` — handler `CompanionCreateOrder` (POST)
    - `cnext_backend/apps/payment/views/payment_views.py:365` — requests.post -> ? (via call)
    - `cnext_backend/apps/payment/views/payment_views.py:135` — reads User
    - `cnext_backend/apps/payment/views/payment_views.py:91` — Order:write
    - `cnext_backend/apps/payment/services/coupon_service.py:110` — <instance>:write (via call)
    - `cnext_backend/apps/payment/models/orders.py:89` — OrderedItems:write (via call)
- **unknown:** db ⚠: <instance>:write, CPProductCampaign:read, CareersProductCouponCode:read, CareersProductCouponPack:read, Order:read, Order:write, OrderedItems:write, ProductEntities:read, UrlAlias:read, User:read — write hidden in manager/service · external ⚠: requests.post -> ? — indirect; dest unresolved · async ⚠: threading.Thread -> send_payment_success_email — pattern-detected; not in a Celery-only view · pii ⚠: reads User — PII source co-occurs with an egress path — object-level taint not traced

### 🔴 NEW ENDPOINT — `api/v1/companions/payment/order-status`
- **why:** new endpoint reads PII and has an egress path
- auth: permission_classes=['IsAuthenticated']
- **flow:** api/v1/companions/payment/order-status → CompanionOrderStatus → {CPProductCampaign:read, Order:read, Order:write, OrderedItems:read, ProductEntities:read, User:read} → ext: requests.post -> settings.PAYTM_STATUS_URL
- **investigate:**
    - `cnext_backend/apps/payment/views/payment_views.py:407` — handler `CompanionOrderStatus` (GET)
    - `cnext_backend/apps/payment/views/payment_views.py:611` — requests.post -> settings.PAYTM_STATUS_URL (via call)
    - `cnext_backend/apps/payment/views/payment_views.py:421` — reads User
    - `cnext_backend/apps/payment/views/payment_views.py:431` — Order:write
    - `cnext_backend/apps/payment/views/payment_views.py:541` — Order:write (via call)
- **unknown:** db ⚠: CPProductCampaign:read, Order:read, Order:write, OrderedItems:read, ProductEntities:read, User:read — write hidden in manager/service · external ⚠: requests.post -> settings.PAYTM_STATUS_URL — indirect; host in settings · async ⚠: threading.Thread -> send_payment_failed_email, threading.Thread -> send_payment_processing_email, threading.Thread -> send_payment_success_email — pattern-detected; not in a Celery-only view · pii ⚠: reads User — PII source co-occurs with an egress path — object-level taint not traced

### 🔴 NEW ENDPOINT — `api/v1/companions/payment/invoice`
- **why:** new endpoint exposes a PII path with open/unspecified auth
- auth: open (AllowAny)
- **flow:** api/v1/companions/payment/invoice → CompanionGetInvoice → {<instance>:write, CPProductCampaign:read, Location:read, Order:read, OrderedItems:read, User:read}
- **investigate:**
    - `cnext_backend/apps/payment/views/invoice_views.py:9` — handler `CompanionGetInvoice` (GET)
    - `cnext_backend/apps/payment/services/invoice_service.py:273` — email
    - `cnext_backend/apps/payment/services/invoice_service.py:190` — reads User
    - `cnext_backend/apps/payment/services/invoice_service.py:218` — <instance>:write (via call)
- **unknown:** db ⚠: <instance>:write, CPProductCampaign:read, Location:read, Order:read, OrderedItems:read, User:read — write hidden in manager/service · pii ?: email, reads User — PII source read; downstream egress not proven

### 🟠 NEW ENDPOINT — `api/v1/companions/payment/product-details`
- **why:** new endpoint on a money/payment path
- auth: permission_classes=['IsAuthenticated']
- **flow:** api/v1/companions/payment/product-details → CompanionProductDetails
- **investigate:**
    - `cnext_backend/apps/payment/views/product_views.py:18` — handler `CompanionProductDetails` (GET)

### 🟠 NEW ENDPOINT — `api/v1/companions/payment/validate-coupon`
- **why:** new endpoint on a money/payment path
- auth: permission_classes=['IsAuthenticated']
- **flow:** api/v1/companions/payment/validate-coupon → CompanionValidateCoupon → {CareersProductCouponPack:read}
- **investigate:**
    - `cnext_backend/apps/payment/views/product_views.py:44` — handler `CompanionValidateCoupon` (POST)
- **unknown:** db ⚠: CareersProductCouponPack:read — write hidden in manager/service

### 🟠 NEW ENDPOINT — `api/v1/companions/payment/callback/<int:order_id>`
- **why:** new endpoint on a money/payment path
- auth: open (AllowAny)
- **flow:** api/v1/companions/payment/callback/<int:order_id> → CompanionPaymentCallback
- **investigate:**
    - `cnext_backend/apps/payment/views/payment_views.py:379` — handler `CompanionPaymentCallback` (POST,GET)

### 🟠 NEW ENDPOINT — `api/v1/companions/order-shipping-address`
- **why:** new endpoint on a money/payment path
- auth: permission_classes=['IsAuthenticated']
- **flow:** api/v1/companions/order-shipping-address → OrderShippingAddressAPIView → {<instance>:write, OrderShippingAddress:read, OrderShippingAddress:write}
- **investigate:**
    - `cnext_backend/apps/payment/views/address_views.py:47` — handler `OrderShippingAddressAPIView` (GET,POST)
    - `cnext_backend/apps/payment/views/address_views.py:142` — <instance>:write
    - `cnext_backend/apps/payment/views/address_views.py:149` — OrderShippingAddress:write
- **unknown:** db ⚠: <instance>:write, OrderShippingAddress:read, OrderShippingAddress:write — write hidden in manager/service

### 🟡 NEW ENDPOINT — `api/token/`
- **why:** new endpoint — handler not resolved in repo (library/external view)
- auth: unspecified
- **flow:** api/token/ → TokenObtainPairView
- **investigate:**
    - `:0` — handler `TokenObtainPairView` (?)

### 🟡 NEW ENDPOINT — `api/token/refresh/`
- **why:** new endpoint — handler not resolved in repo (library/external view)
- auth: unspecified
- **flow:** api/token/refresh/ → TokenRefreshView
- **investigate:**
    - `:0` — handler `TokenRefreshView` (?)

### 🟡 NEW ENDPOINT — `api/<int:version>/announcements/read`
- **why:** new endpoint with a DB write / external call
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/announcements/read → MarkAnnouncementReadAPI → {Announcement:read, AnnouncementEntityMapping:read, UserAnnouncementRead:read, UserAnnouncementRead:write}
- **investigate:**
    - `cnext_backend/apps/companions/api/announcements/controllers.py:426` — handler `MarkAnnouncementReadAPI` (POST)
    - `cnext_backend/apps/companions/api/announcements/controllers.py:480` — UserAnnouncementRead:write
- **unknown:** db ⚠: Announcement:read, AnnouncementEntityMapping:read, UserAnnouncementRead:read, UserAnnouncementRead:write — write hidden in manager/service

### 🟡 CHANGED — `api/<int:version>/college-predictor/cutoff-index/rebuild`
- **why:** new async dispatch: threading.Thread -> service.run_sync_compainion_cutoof_data
- **flow:** api/<int:version>/college-predictor/cutoff-index/rebuild → CutoffIndexRebuildAPI
- **investigate:**
    - `cnext_backend/apps/college_predictor/api/controllers.py:136` — handler `CutoffIndexRebuildAPI` (POST)
    - `cnext_backend/apps/college_predictor/api/controllers.py:167` — threading.Thread -> service.run_sync_compainion_cutoof_data
- **unknown:** async ⚠: threading.Thread -> service.run_sync_compainion_cutoof_data — pattern-detected; not in a Celery-only view

### 🟢 NEW ENDPOINT — `api/<int:version>/college-predictor/seat-allotment/tuples`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/college-predictor/seat-allotment/tuples → SeatAllotmentTuplesAPI → {CnextUserCourse:read}
- **investigate:**
    - `cnext_backend/apps/college_predictor/seat_allotment/apis/tuple_results_api.py:92` — handler `SeatAllotmentTuplesAPI` (POST)
- **unknown:** db ⚠: CnextUserCourse:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/college-predictor/seat-allotment/search`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/college-predictor/seat-allotment/search → SeatAllotmentSearchAPI → {CnextUserCourse:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/college_predictor/seat_allotment/apis/tuple_results_api.py:133` — handler `SeatAllotmentSearchAPI` (POST)
- **unknown:** db ⚠: CnextUserCourse:read, UrlAlias:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/college-predictor/seat-allotment/filter-options`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/college-predictor/seat-allotment/filter-options → SeatAllotmentFilterOptionsAPI → {CnextUserCourse:read}
- **investigate:**
    - `cnext_backend/apps/college_predictor/seat_allotment/apis/tuple_results_api.py:158` — handler `SeatAllotmentFilterOptionsAPI` (POST)
- **unknown:** db ⚠: CnextUserCourse:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/college-predictor/seat-allotment/dropdowns`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/college-predictor/seat-allotment/dropdowns → SeatAllotmentDropdownsAPI → {CnextUserCourse:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/college_predictor/seat_allotment/apis/tuple_results_api.py:212` — handler `SeatAllotmentDropdownsAPI` (POST)
- **unknown:** db ⚠: CnextUserCourse:read, UrlAlias:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/college-predictor/seat-allotment/college-seat-data`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/college-predictor/seat-allotment/college-seat-data → SeatAllotmentCollegeSeatAPI → {CPProductCampaign:read, CollegeCourse:read, Degrees:read, Exam:read, Location:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/college_predictor/seat_allotment/apis/college_seat_api.py:17` — handler `SeatAllotmentCollegeSeatAPI` (GET)
- **unknown:** db ⚠: CPProductCampaign:read, CollegeCourse:read, Degrees:read, Exam:read, Location:read, UrlAlias:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/college-predictor/seat-allotment/cache-flush`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/college-predictor/seat-allotment/cache-flush → SeatAllotmentCacheFlushAPI → {UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/college_predictor/seat_allotment/apis/tuple_results_api.py:272` — handler `SeatAllotmentCacheFlushAPI` (POST)

### 🟢 NEW ENDPOINT — `api/<int:version>/college-predictor/seat-allotment/cutoff-data-sync`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/college-predictor/seat-allotment/cutoff-data-sync → CutoffDataSyncAPI → {CPCutoffFinal:read}
- **investigate:**
    - `cnext_backend/apps/college_predictor/seat_allotment/apis/tuple_results_api.py:346` — handler `CutoffDataSyncAPI` (POST)
- **unknown:** async ⚠: threading.Thread -> _run_sync — pattern-detected; not in a Celery-only view

### 🟢 NEW ENDPOINT — `api/<int:version>/announcements/dashboard`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/announcements/dashboard → AnnouncementDashboardAPI → {Announcement:read, AnnouncementEntityMapping:read, UserAnnouncementRead:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/announcements/controllers.py:21` — handler `AnnouncementDashboardAPI` (GET)
- **unknown:** db ⚠: Announcement:read, AnnouncementEntityMapping:read, UserAnnouncementRead:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/announcements/list`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/announcements/list → AnnouncementListAPI → {Announcement:read, AnnouncementEntityMapping:read, UserAnnouncementRead:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/announcements/controllers.py:88` — handler `AnnouncementListAPI` (GET)
- **unknown:** db ⚠: Announcement:read, AnnouncementEntityMapping:read, UserAnnouncementRead:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/announcements/filters`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/announcements/filters → AnnouncementFiltersAPI → {Announcement:read, AnnouncementEntityMapping:read, AnnouncementProduct:read, UserAnnouncementRead:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/announcements/controllers.py:294` — handler `AnnouncementFiltersAPI` (GET)

### 🟢 NEW ENDPOINT — `api/<int:version>/dashboard/summary`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/dashboard/summary → DashboardSummaryAPI → {Announcement:read, AnnouncementEntityMapping:read, AnnouncementProduct:read, UserAnnouncementRead:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/dashboard/controllers.py:48` — handler `DashboardSummaryAPI` (GET)
- **unknown:** db ⚠: Announcement:read, AnnouncementEntityMapping:read, AnnouncementProduct:read, UserAnnouncementRead:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/dashboard/prediction-stats`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/dashboard/prediction-stats → DashboardPredictionStatsAPI → {CPProductCampaign:read, DashboardExamDates:read, Exam:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/dashboard/controllers.py:112` — handler `DashboardPredictionStatsAPI` (GET)
- **unknown:** db ⚠: CPProductCampaign:read, DashboardExamDates:read, Exam:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/companion_landing/home-page`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/companion_landing/home-page → CompanionHomePageAPI → {CPProductCampaign:read, LandingPageTestimonial:read, MetaTags:read, ToolsLandingPage:read, ToolsLandingPageFAQ:read, ToolsLandingPageSection:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/landing/controllers.py:19` — handler `CompanionHomePageAPI` (GET)

### 🟢 NEW ENDPOINT — `api/<int:version>/companion_landing/application-open-listing`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/companion_landing/application-open-listing → ApplicationOpenListingAPI → {CPCutoffFinal:read, CPProductCampaign:read, CollegeCourse:read, Exam:read, ExamApplication:read, ExamDates:read, ExamMode:read, PreferredEducationLevel:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/landing/controllers.py:361` — handler `ApplicationOpenListingAPI` (GET)

### 🟢 NEW ENDPOINT — `api/<int:version>/companion_landing/application-open-filters`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/companion_landing/application-open-filters → ApplicationOpenFiltersAPI → {CPCutoffFinal:read, CPProductCampaign:read, CollegeCourse:read, Exam:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/landing/controllers.py:1040` — handler `ApplicationOpenFiltersAPI` (GET)

### 🟢 NEW ENDPOINT — `api/<int:version>/companion_landing/application-open-search`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/companion_landing/application-open-search → ApplicationOpenSearchAPI → {CPCutoffFinal:read, CPProductCampaign:read, CollegeCourse:read, Exam:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/landing/controllers.py:1411` — handler `ApplicationOpenSearchAPI` (GET)

### 🟢 NEW ENDPOINT — `api/<int:version>/companion_landing/companion-purchase-check`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/companion_landing/companion-purchase-check → CompanionPurchaseCheckAPI → {CPProductCampaign:read, CampaignPage:read, Domain:read, Order:read, OrderedItems:read, ProductEntities:read, ProductSession:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/landing/controllers.py:2198` — handler `CompanionPurchaseCheckAPI` (GET)
- **unknown:** db ⚠: CPProductCampaign:read, CampaignPage:read, Domain:read, Order:read, OrderedItems:read, ProductEntities:read, ProductSession:read, UrlAlias:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/companion_landing/companion-recommended-tools-cp`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/companion_landing/companion-recommended-tools-cp → CompanionRecommendedToolsCP → {CPProductCampaign:read, CnextProductMapping:read, CpProductCampaignItems:read, Domain:read, Exam:read, ExamDates:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/landing/controllers.py:2423` — handler `CompanionRecommendedToolsCP` (GET)
- **unknown:** db ⚠: CPProductCampaign:read, CnextProductMapping:read, CpProductCampaignItems:read, Domain:read, Exam:read, ExamDates:read, UrlAlias:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/companion_landing/companion-recommended-tools-rp`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/companion_landing/companion-recommended-tools-rp → CompanionRecommendedToolsRP → {CPProductCampaign:read, Domain:read, Exam:read, ExamDates:read, PreferredEducationLevel:read, UrlAlias:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/landing/controllers.py:2619` — handler `CompanionRecommendedToolsRP` (GET)
- **unknown:** db ⚠: CPProductCampaign:read, Domain:read, Exam:read, ExamDates:read, PreferredEducationLevel:read, UrlAlias:read — write hidden in manager/service

### 🟢 NEW ENDPOINT — `api/<int:version>/companion_landing/registration-prefill`
- **why:** new read-only endpoint
- auth: permission_classes=['ApiKeyPermission']
- **flow:** api/<int:version>/companion_landing/registration-prefill → ToolRegistrationPrefillAPI → {ProductSession:read, ToolRegistrationFlowEntry:read, ToolsLandingPage:read}
- **investigate:**
    - `cnext_backend/apps/companions/api/registration/controllers.py:33` — handler `ToolRegistrationPrefillAPI` (GET)

---
_Blindspot: this diff sees routing/auth/db/external/async at the endpoint lens; it does not see logic inside a function, ordering, concurrency, or off-by-one._