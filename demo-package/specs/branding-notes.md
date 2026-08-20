# Branding notes — "Frappe CRM" to "PARAMA CRM"

Date: 2026-08-18. Branch: `feat/feature-expansion`. Working tree only, not committed.

Scope: display-level rebrand. No Python package, module path, doctype name, or
route changed. Upstream licence attribution stays (this product is a fork of
Frappe CRM, AGPLv3).

---

## 1. Brand assets

### Design sources (swap these to change the logo)

| File | What it is |
| --- | --- |
| `frontend/src/images/parama-mark.svg` | Square mark. Abstract travel glyph (paper plane), 2 strokes, no fill, no gradient, `currentColor`. |
| `frontend/src/images/parama-wordmark.svg` | "PARAMA" wordmark. Geometric sans built from stroked centrelines, letter-spaced, `currentColor`. No font dependency. |

### What the app actually renders

| File | Role |
| --- | --- |
| `frontend/src/components/Icons/ParamaMark.vue` | Same geometry as `parama-mark.svg`. |
| `frontend/src/components/Icons/ParamaWordmark.vue` | Same geometry as `parama-wordmark.svg`. |
| `frontend/src/components/Icons/CRMLogo.vue` | Thin alias that renders `ParamaMark`. Every call site imports `CRMLogo`, so this one file is the app-wide swap point. |

The `.svg` files and the `.vue` files hold the same geometry. Keep them in sync,
or replace both when a real logo arrives.

**To swap in a real logo — three options, cheapest first:**

1. **No code.** Settings > Brand: upload a logo and a favicon. `FCRM Settings`
   holds `brand_name`, `brand_logo`, `favicon`. `BrandLogo.vue` prefers the
   uploaded logo and falls back to `CRMLogo.vue`. `UserDropdown.vue` prefers
   `brand_name` and falls back to the string `PARAMA CRM`.
2. **One file.** Point `frontend/src/components/Icons/CRMLogo.vue` at a
   different icon component. Sidebar, About modal, onboarding card, and theme
   switcher all follow.
3. **Full swap.** Replace `parama-mark.svg` + `ParamaMark.vue` (and the wordmark
   pair), then regenerate the raster assets — see section 2.

Brand colour used by the raster tiles: `#4f46e5` (indigo-600). It matches
`--surface-gray-10` in `frontend/src/index.css`.

## 2. Raster assets (regenerated from the same geometry)

Generator script (kept out of the repo, in the session scratchpad):
`/tmp/claude-1000/-home-kreshnith-CRM/fc5d822f-861e-4bc5-84c2-039ac06f04ca/scratchpad/gen_brand_assets.py`.
It uses Pillow 11.3.0 (`python3 -m PIL`, already installed on the host). Copy it
into the repo if the logo changes again, or redraw by hand.

| File | Size | Note |
| --- | --- | --- |
| `frontend/public/favicon.png` | 64x64 | Browser tab icon. |
| `crm/public/manifest/apple-icon-180.png` | 180x180 | iOS home-screen icon. |
| `crm/public/manifest/manifest-icon-192.maskable.png` | 192x192 | PWA icon, wider safe zone. |
| `crm/public/manifest/manifest-icon-512.maskable.png` | 512x512 | PWA icon, wider safe zone. |
| `crm/public/manifest/apple-splash-*.jpg` | 34 files | iOS PWA launch screens. The script finds the old logo box in each file and draws the new tile in exactly that box, so the layout does not move. |
| `crm/public/images/logo.svg` | 300x300 | Desk app-switcher icon. Referenced by `crm/hooks.py` `app_icon_url` — the path is unchanged, only the file content. |
| `crm/public/images/logo.png` | 1200x1200 | Same mark, raster. |

**Favicon outcome:** the task brief said `frontend/public/favicon.png` still held
the Frappe favicon. That was not true. Commit `62703787` ("feat: geometric P
monogram brand mark") had already replaced it with an indigo "P" monogram tile.
It is now the paper-plane mark, so the favicon matches the rest of the identity.
Revert with `git checkout -- frontend/public/favicon.png` if the "P" monogram is
preferred.

The 34 splash screens and the 3 manifest/apple icons **did** still carry the
Frappe magenta funnel logo. They are now the PARAMA mark.

## 3. Files changed — display strings

| File | Change |
| --- | --- |
| `frontend/index.html` | `<title>` and `apple-mobile-web-app-title` to `PARAMA CRM`. |
| `frontend/vite.config.js` | PWA manifest `name`, `short_name`, `description`. Added `theme_color: '#4f46e5'` and `background_color: '#ffffff'` (was the Vue-green default `#42b883`). |
| `frontend/src/components/Icons/CRMLogo.vue` | Now an alias for `ParamaMark`. |
| `frontend/src/components/Modals/AboutModal.vue` | Heading to `PARAMA CRM`; added the AGPLv3 attribution line (see section 6). |
| `frontend/src/components/UserDropdown.vue` | Sidebar brand-name fallback `'CRM'` to `'PARAMA CRM'`. |
| `frontend/src/components/Settings/PreferencesSettings.vue` | Theme-switcher brand-name fallback, same change. |
| `frontend/src/components/Questionnaire.vue` | Onboarding card header is now a mark + wordmark lockup. |
| `frontend/src/components/Layouts/AppSidebar.vue` | Help article group `Frappe CRM mobile` to `PARAMA CRM mobile`. |
| `frontend/src/components/Settings/ERPNextSettings.vue` | `Connect ERPNext to Frappe CRM` to `... to PARAMA CRM`. This is a heading, not an identifier. |
| `frontend/src/pages/PersonaForm.vue` | Question text and page title. |
| `frontend/src/pages/NotPermitted.vue` | Access-denied text. |
| `crm/www/crm.py` | Permission-error message. |
| `crm/templates/emails/crm_invitation.html` | Invitation email body. |

Build outputs regenerated by `yarn build` and **git-ignored** (do not commit,
they rebuild): `crm/www/crm.html`, `crm/public/frontend/**`,
`frontend/components.d.ts`.

`frontend/src/pages/Welcome.vue` needed no change — it carries no brand string.
There is no in-repo login page; Frappe serves `/login` (see section 5).

## 4. Deliberately left unchanged

| Where | Why |
| --- | --- |
| `useOnboarding('frappecrm')` in 11 files | Technical app key sent to the frappe-ui onboarding service. Renaming it breaks onboarding state. |
| `© Frappe Technologies Pvt. Ltd.` in the About modal | Upstream copyright. Required by the licence. |
| Copyright headers in every `.py` / `.js` / `.json` source file | Same reason. |
| About-modal links: `frappe.io/crm`, `github.com/frappe/crm`, `docs.frappe.io/crm`, `support.frappe.io` | Upstream project links. |
| `docsLink="https://docs.frappe.io/crm"` in `AppSidebar.vue` | Upstream documentation. |
| `friendly_resource_name = "Frappe CRM"` in `crm/fcrm/doctype/crm_twilio_settings/crm_twilio_settings.py` | Technical identifier: the name Twilio stamps on the TwiML app and API keys it creates. Changing it on a live account orphans the existing Twilio resources. Out of the sweep scope. |
| `crm/hooks.py` | Owned by another worker. See section 5. |
| `README.md` (`.github/logo.svg`, "Frappe CRM Logo") | Repository documentation, not product UI. |
| `crm/public/images/desk.png` | A demo screenshot attachment, not a brand asset. |

## 5. Changes that must be applied elsewhere

### 5.1 `crm/hooks.py` — not applied (file owned by another worker)

Apply this diff when that file is free:

```diff
-app_title = "Frappe CRM"
+app_title = "PARAMA CRM"
-app_description = "Kick-ass Open Source CRM"
+app_description = "Travel sales CRM — leads, itineraries and follow-ups in one place"
```

Leave `app_name = "crm"` (the Python package name), `app_publisher`,
`app_email`, and `app_license` exactly as they are. `app_icon_url` and the
`"logo"` entry on line 18 need **no edit**: they point at
`/assets/crm/images/logo.svg`, and that file is already the new mark.

There is no `app_logo_url` hook in this file.

### 5.2 Login page and desk branding — database, not the repo

Frappe builds `/login` from `Website Settings.app_name` (falling back to
`System Settings.app_name`) and from `Navbar Settings.app_logo`. Verified by
reading `apps/frappe/frappe/www/login.py` lines 51-53 and `login.html` line 63
inside the `crm-local-frappe-1` container. Nothing in this repo controls it.

Run these on the demo site. They write to the database, so they were **not** run
here:

```bash
docker exec -it crm-local-frappe-1 bash -lc 'cd ~/frappe-bench && \
  bench --site crm.localhost execute frappe.client.set_value --kwargs "{\"doctype\":\"Website Settings\",\"name\":\"Website Settings\",\"fieldname\":\"app_name\",\"value\":\"PARAMA CRM\"}"'

docker exec -it crm-local-frappe-1 bash -lc 'cd ~/frappe-bench && \
  bench --site crm.localhost execute frappe.client.set_value --kwargs "{\"doctype\":\"Navbar Settings\",\"name\":\"Navbar Settings\",\"fieldname\":\"app_logo\",\"value\":\"/assets/crm/images/logo.svg\"}"'

docker exec -it crm-local-frappe-1 bash -lc 'cd ~/frappe-bench && \
  bench --site crm.localhost execute frappe.client.set_value --kwargs "{\"doctype\":\"Website Settings\",\"name\":\"Website Settings\",\"fieldname\":\"favicon\",\"value\":\"/assets/crm/manifest/apple-icon-180.png\"}"'

docker exec -it crm-local-frappe-1 bash -lc 'cd ~/frappe-bench && bench --site crm.localhost clear-cache'
```

The same three values are editable in the desk UI: Website Settings and Navbar
Settings.

### 5.3 In-app brand name — optional, database

`FCRM Settings.brand_name` overrides the sidebar name. Set it to keep the
sidebar label stable even if the code fallback changes later:

```bash
docker exec -it crm-local-frappe-1 bash -lc 'cd ~/frappe-bench && \
  bench --site crm.localhost execute frappe.client.set_value --kwargs "{\"doctype\":\"FCRM Settings\",\"name\":\"FCRM Settings\",\"fieldname\":\"brand_name\",\"value\":\"PARAMA CRM\"}"'
```

## 6. Licence attribution

`frontend/src/components/Modals/AboutModal.vue` now shows two lines under the
links:

```
Built on Frappe CRM (AGPLv3) — github.com/frappe/crm
© Frappe Technologies Pvt. Ltd. and contributors
```

No `LICENSE` file and no source-file copyright header was touched.

## 7. Public web form footer

`crm/www/crm_form.html` has **no** "Powered by Frappe" footer and no brand
mark. It renders only the form title, the fields, and the buttons. No change was
needed. Checked with `grep -rn "Powered by" frontend/src crm/www crm/templates`
— zero hits anywhere in the repo.

## 8. Verification performed

- `cd frontend && yarn build` — passed. The default Node heap is too small for
  this build on this host; it needs `NODE_OPTIONS=--max-old-space-size=5120`.
  This limit is pre-existing and unrelated to the rebrand.
- `cd frontend && yarn test:run` — `Test Files 10 passed (10)`,
  `Tests 224 passed (224)`.
- `grep -rn "Frappe CRM" frontend/src crm/www crm/templates frontend/index.html frontend/vite.config.js`
  — only the two intended hits in `AboutModal.vue` (the attribution string and
  the comment above it).
- Built output confirmed: `crm/www/crm.html` line 9 is `<title>PARAMA CRM</title>`;
  `crm/public/frontend/manifest.webmanifest` reads
  `"name":"PARAMA CRM","short_name":"PARAMA CRM"`, `"theme_color":"#4f46e5"`.

## 9. Deployment — not done

The running demo container `crm-local-frappe-1` keeps its **own** copy of the
app in the `crm-local_bench-data` volume at
`/home/frappe/frappe-bench/apps/crm`. The host repo is mounted read-through at
`/workspace/app`, so nothing in this change is live yet. Confirmed: the
container still serves the 3499-byte Frappe `apple-icon-180.png`, while the host
file is now 7600 bytes.

Run `docker/deploy-local.sh` to push the change to the demo site. That restarts
a live service, so it was left to the owner.

Also confirmed on the live site, which supports section 5.2:
`curl http://localhost:8000/login` returns `<title>Login</title>` and
`app-logo" src="/assets/frappe/images/frappe-framework-logo.svg"`. Both come
from the database, not from this repo.
