# EduQGen — Public Go-Live Test Cases

**File for execution (Pass/Fail dropdown):** [`EduQGen_Public_GoLive_TestCases.xlsx`](EduQGen_Public_GoLive_TestCases.xlsx)

| Priority | Meaning |
|----------|---------|
| **P0** | Must Pass before public share |
| **P1** | Important; fix soon |
| **P2** | Nice-to-have / deeper API |

**Pass/Fail values:** `Pass` · `Fail` · `Blocked` · `N/A`

**Roles to use:** Admin · Org Admin · User A · User B (same org)

---

## Smoke path (30–45 min)

1. Login as User A via **public URL**
2. Upload 1 EN PDF → wait `ready`
3. Upload 1 PYQ PDF → wait `ready`
4. Generate (PDF + PYQ, EN, 2–3 Qs)
5. Complete human review
6. Export CSV + JSON
7. Org Admin: browse User A; hit one quota limit
8. Admin: view chunks; check Prompts defaults
9. Logout / wrong password / cross-user URL deny
10. Confirm Celery + default ModelConfig healthy

---

## Test case list

### Auth & access (P0–P1)

| ID | Pri | Role | Test Case | Pass/Fail |
|----|-----|------|-----------|-----------|
| TC-001 | P0 | All | Login with valid credentials | |
| TC-002 | P0 | All | Login with wrong password | |
| TC-003 | P0 | All | Logout | |
| TC-004 | P1 | All | Unauthenticated access blocked | |
| TC-005 | P1 | Public | Signup creates account in an org | |
| TC-006 | P0 | User | User cannot open Prompts | |
| TC-007 | P0 | User | User cannot open Control | |
| TC-008 | P0 | Org Admin | Org Admin: Control OK, Prompts denied | |
| TC-009 | P0 | Admin | Admin sees Prompts, Control, Django Admin | |

### Dashboard / tunnel

| ID | Pri | Role | Test Case | Pass/Fail |
|----|-----|------|-----------|-----------|
| TC-010 | P0 | All | Dashboard loads (org + role) | |
| TC-011 | P1 | All | Nav links work | |
| TC-012 | P0 | All | Public tunnel CSRF (login + upload POST) | |

### PDF module

| ID | Pri | Role | Test Case | Pass/Fail |
|----|-----|------|-----------|-----------|
| TC-013 | P0 | User | Upload single PDF → ready | |
| TC-014 | P0 | User | Upload ZIP with PDFs → ready | |
| TC-015 | P0 | User | Reject non-PDF/ZIP | |
| TC-016 | P0 | User | Reject ZIP with no PDFs | |
| TC-017 | P1 | User | Chunk strategies (fixed/sentence/recursive/…) | |
| TC-018 | P1 | User | Custom chunk size / overlap | |
| TC-019 | P0 | User | Status polling while indexing | |
| TC-020 | P0 | Admin | View chunks Admin-only | |
| TC-021 | P1 | Admin | Reindex PDF | |
| TC-022 | P0 | User | Delete PDF; storage freed | |
| TC-023 | P0 | User | PDF count quota exceeded | |
| TC-024 | P0 | User | Storage GB quota exceeded | |
| TC-025 | P1 | Admin | Hindi / legacy font content readable | |
| TC-026 | P1 | Admin | Broken Latin EN repair (e.g. BEGC-102) | |
| TC-027 | P0 | All | Browse filter isolation | |
| TC-028 | P0 | User | Cannot open another user’s PDF URL | |
| TC-029 | P1 | All | Celery down → stays pending | |

### PYQ module

| ID | Pri | Role | Test Case | Pass/Fail |
|----|-----|------|-----------|-----------|
| TC-030 | P0 | User | Upload TEE PYQ → ready + questions | |
| TC-031 | P0 | User | Upload assignment PYQ → ready | |
| TC-032 | P0 | User | Reject non-PDF | |
| TC-033 | P0 | User | Detail: types, marks, pagination | |
| TC-034 | P1 | User | MCQ options display | |
| TC-035 | P0 | User | Edit question | |
| TC-036 | P0 | User | Delete single question | |
| TC-037 | P0 | User | Delete module; storage freed | |
| TC-038 | P0 | User | PYQ count quota exceeded | |
| TC-039 | P0 | User | Empty/unreadable PDF → error | |
| TC-040 | P0 | Admin | Default ModelConfig required for extract | |
| TC-041 | P0 | All | PYQ browse isolation | |
| TC-042 | P1 | User | Hindi PYQ extract | |

### Prompts (Admin)

| ID | Pri | Role | Test Case | Pass/Fail |
|----|-----|------|-----------|-----------|
| TC-043 | P0 | Admin | Create prompt | |
| TC-044 | P0 | Admin | Edit creates version | |
| TC-045 | P0 | Admin | Activate exclusive prompt | |
| TC-046 | P1 | Admin | Duplicate prompt | |
| TC-047 | P1 | Admin | Delete prompt | |
| TC-048 | P0 | Admin | Default + Hindi Generator exist | |

### Question generation

| ID | Pri | Role | Test Case | Pass/Fail |
|----|-----|------|-----------|-----------|
| TC-049 | P0 | User | New run PDF+PYQ (EN) | |
| TC-050 | P0 | User | New run Hindi | |
| TC-051 | P0 | User | Progress polling | |
| TC-052 | P0 | User | Human review gate | |
| TC-053 | P0 | User | Results / question cards | |
| TC-054 | P1 | User | Edit / delete generated Q | |
| TC-055 | P0 | User | Export CSV | |
| TC-056 | P0 | User | Export JSON | |
| TC-057 | P1 | User | Export DOCX | |
| TC-058 | P1 | Admin | Export dataset JSON | |
| TC-059 | P1 | User | Retry failed/partial | |
| TC-060 | P0 | User | Delete batch run | |
| TC-061 | P0 | User | Credit pre-check blocks overspend | |
| TC-062 | P0 | User | Credits deducted after run | |
| TC-063 | P1 | User | Daily / concurrent limits | |
| TC-064 | P0 | Admin | Advanced settings Admin-only | |
| TC-065 | P1 | Admin | Think / council mode | |
| TC-066 | P1 | User | PDF-only run | |
| TC-067 | P1 | User | PYQ-only run | |
| TC-068 | P0 | Org Admin | New Run picker vs My uploads | |
| TC-069 | P0 | User | Cannot open another user’s run | |

### Control & quotas

| ID | Pri | Role | Test Case | Pass/Fail |
|----|-----|------|-----------|-----------|
| TC-070 | P0 | Admin | Create org + policy | |
| TC-071 | P0 | Admin | Create Org Admin | |
| TC-072 | P0 | Org Admin | Create User + quotas | |
| TC-073 | P0 | Org Admin | Edit user quota enforced | |
| TC-074 | P1 | Org Admin | Deactivate member | |
| TC-075 | P1 | Admin | Unlimited admin display | |

### API

| ID | Pri | Role | Test Case | Pass/Fail |
|----|-----|------|-----------|-----------|
| TC-076 | P1 | Auth | GET `/api/users/me/` | |
| TC-077 | P1 | User | GET `/api/pdf/contexts/` | |
| TC-078 | P1 | User | GET `/api/pyq/modules/` | |
| TC-079 | P2 | Admin | Org CRUD via API | |
| TC-080 | P2 | User | Generate runs API | |

### Ops / go-live

| ID | Pri | Role | Test Case | Pass/Fail |
|----|-----|------|-----------|-----------|
| TC-081 | P0 | Admin | Celery worker running | |
| TC-082 | P0 | Admin | Redis + Postgres healthy | |
| TC-083 | P0 | Admin | Default LLM + embed configured | |
| TC-084 | P0 | All | Full smoke on public URL | |
| TC-085 | P1 | All | Mobile viewport usable | |
| TC-086 | P1 | All | ngrok “Visit Site” interstitial | |
| TC-087 | P0 | Admin | Demo accounts ready | |
| TC-088 | P1 | Admin | EN + HI sample content ready | |

---

## Known risks (check during Fail notes)

- Celery down → PDF/PYQ/generate stuck `pending`
- No default `ModelConfig` → PYQ extract never finishes
- CSRF on tunnel if host not trusted
- Chunks View = Admin only
- Human review required before full results
- Org Admin New Run may not list **own** uploads (org Users’ ready content)

**Totals:** 88 cases · P0 must all Pass before public go-live.
