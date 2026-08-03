# EduQGen walkthrough scripts (record from these)

Use a clean browser (or incognito), zoom ~110%, hide bookmarks bar.  
Target length: **4–6 minutes per video**.

**Demo logins** (org: **DEMO-ORG**)
| Role | Username | Password |
|------|----------|----------|
| Org Admin | `demoadmin` | `DemoAdmin123!` |
| User | `demouser` | `DemoUser123!` |

*(Older lab accounts: `user123` / `psbgen123`, `ps123` / `bgen1234` on org `ps`.)*

Prepare beforehand: one small English PDF, one PYQ PDF (or reuse ready contexts).

---

## Video 1 — User walkthrough (~5 min)

**Title suggestion:** *EduQGen — Instructor / User walkthrough*

| Time | Screen | What to show / say |
|------|--------|--------------------|
| 0:00 | Login | “This is the EduQGen user workspace. I’ll sign in as an instructor.” Login as `user123`. |
| 0:20 | Dashboard | Point out org + role. Cards: PDF Contexts, PYQ Bank, Generate Questions. Note: no Control / Prompts (admin-only). |
| 0:40 | PDF Contexts | Open PDF list. Show **Storage** + **PDF uploads** rings. Click **Upload Context**. |
| 1:00 | Upload PDF | Name the context, drop PDF, submit. Wait until status is **ready**. |
| 1:40 | PYQ Bank | Upload a past paper. Wait until **ready**. Briefly open detail (extracted questions). |
| 2:20 | Generate | **New Batch Run**: name + topic, select PDF + PYQ, language EN, optional Think, 2–3 questions. Submit. |
| 2:50 | Progress | Show progress panel polling until complete. |
| 3:10 | Review *(if feedback enabled)* | Approve/reject one question; mention comment required on reject. If feedback is off in Technical settings, skip and open results. |
| 3:50 | Results | Question cards, optional Think badge. Export **CSV** / **JSON**. |
| 4:20 | Regenerate | If remaining slots: **Generate remaining**. |
| 4:40 | Close | Logout. “Users create content and generate questions within their quotas.” |

**Do not show:** Control, Technical settings, Prompts, Django Admin, other users’ data.

---

## Video 2 — Org Admin walkthrough (~5 min)

**Title suggestion:** *EduQGen — Organisation Admin walkthrough*

| Time | Screen | What to show / say |
|------|--------|--------------------|
| 0:00 | Login | “Org Admins manage users and quotas for their organisation.” Login as `ps123`. |
| 0:20 | Dashboard | Role = Org Admin. Same content tools + **Control**. |
| 0:40 | Control | Open Control → **User management**. Show credits/storage pool remaining. |
| 1:00 | Create user | **+ New user**: username, password, credits, storage GB. Save. |
| 1:40 | Edit quota | Open a user → Edit quota: credits, storage, PDF/PYQ limits, daily runs, **Account active** switch. |
| 2:10 | Deactivate (optional 20s) | Turn Account active off → say user sees “account deactivated” on login. Turn back on. |
| 2:40 | Browse as admin | PDF Contexts → filter **a user** (or My PDFs). Same for PYQ. “Org Admins can review member content.” |
| 3:20 | Own generate path | Briefly: Org Admin can also upload & generate (uses **unallocated** pool). Don’t need full generation if time is short. |
| 4:00 | What they can’t do | No Prompts, no org-wide Technical settings, no creating other Org Admins (platform Admin only). |
| 4:30 | Close | Logout. “Org Admins distribute pools; Users consume their assigned share.” |

**Do not show:** Technical settings model pickers, Prompt editor, Django Admin (platform Admin video only).

---

## Recording tips

1. Record **User first**, then Org Admin (reset quotas if you created a demo user).
2. Prefer **ready** PDFs already indexed so the video doesn’t wait on Celery.
3. If Think mode is slow, leave it **off** for the User video.
4. Turn **User feedback** on in Technical settings if you want to show the review step; off for a shorter path.
5. Export once; don’t dwell on every question card.

## Optional third video (not requested)

Platform **Admin**: orgs/pools, Org Admins, Technical settings (models + feedback toggle), Prompts.
