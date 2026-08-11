# Frappe CRM — Application Workflow & Architecture Guide

This document provides a comprehensive overview of the end-to-end architecture, entity lifecycle, feature workflows, frontend/backend communication, and scripting engine of **Frappe CRM**.

---

## 1. High-Level Architecture

Frappe CRM is built as a single-page application (SPA) integrated into the Frappe Framework backend ecosystem.

```
┌─────────────────────────────────────────────────────────────┐
│                      Browser / Client                       │
│  Vue 3 + frappe-ui (Vite dev server @ http://localhost:8080) │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP / WebSocket (Proxy)
┌──────────────────────────────▼──────────────────────────────┐
│                    Frappe Python Backend                    │
│             (Bench server @ http://localhost:8000)          │
│                                                             │
│  ┌────────────┐     ┌──────────────┐     ┌──────────────┐   │
│  │ CRM Leads  │     │ CRM Deals    │     │ WhatsApp API │   │
│  └────────────┘     └──────────────┘     └──────────────┘   │
│  ┌────────────┐     ┌──────────────┐     ┌──────────────┐   │
│  │ Contacts   │     │ Tasks & Notes│     │ Call Logs    │   │
│  └────────────┘     └──────────────┘     └──────────────┘   │
└──────────────────────────────┬──────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────┐
│                     Persistence & Queue                     │
│               MariaDB 10.8  │  Redis (Cache & Jobs)         │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Core Business Workflows & Entity Lifecycles

### A. Lead Management Lifecycle
```
[ Incoming Prospect ] ──────► [ CRM Lead Created ]
                                      │
                      ┌───────────────┼───────────────┐
                      ▼               ▼               ▼
                 [ In Process ]  [ Unqualified ]  [ Converted ]
                                                      │
                                                      ▼
                                             [ Opportunity / Deal ]
```
1. **Lead Ingestion & Creation**: Leads can be entered manually, imported via CSV (`DataImport.vue`), created via API, or generated automatically via integrations (WhatsApp / Meta / Webhooks).
2. **Engagement & Qualification**: Sales reps log interactions, schedule calls/tasks, write notes, and send WhatsApp messages directly from the Lead detail view (`Lead.vue`).
3. **Status Progression**: Leads transition through predefined stages (e.g. *Open, In Process, Qualified, Unqualified, Converted*).
4. **Conversion to Deal**: Converting a qualified lead automatically provisions linked `CRM Deal`, `Contact`, and `Organization` records.

---

### B. Deal & Pipeline Management
```
[ Deal Created ] ──► [ Pipeline Stage 1 ] ──► [ Stage 2 ] ──► [ Closed Won ]
                           │                                ──► [ Closed Lost ]
                           └──► SLA Warning / Idle Alert
```
1. **Kanban & List Views**: Sales managers and representatives visualize opportunities via Kanban boards or searchable lists (`Deals.vue`).
2. **Custom Views & Filters**: Users create custom filter views, sort options, and group deals by sales team or territory.
3. **Detail Workspace**: Clicking a deal opens `Deal.vue`, presenting dynamic field layouts, deal value tracking, expected close dates, activity timelines, and linked documents.
4. **Win/Loss Tracking**: Final stage transitions record reasons for deal closure to fuel sales analytics.

---

### C. WhatsApp Communication Inbox Workflow
```
[ Incoming WhatsApp Msg ] ──► [ Webhook API ] ──► [ Store Message ]
                                                         │
                                                         ▼
[ Agent Outbound Msg ] ◄─── [ Live Updates ] ◄── [ WhatsApp Inbox UI ]
```
1. **Omnichannel Inbox**: Unified conversation view (`WhatsAppInbox.vue`) where agents handle customer chats in real time.
2. **Lead/Contact Linkage**: Incoming messages automatically match existing phone numbers to corresponding `CRM Lead` or `Contact` records, showing customer metadata in the chat sidebar.
3. **Follow-ups & Automations**: Backend handlers (`whatsapp_followups.py`) facilitate automated template triggers, message scheduling, and agent re-assignment.

---

### D. Tasks, Notes, Call Logs & Calendar
- **Task Assignment**: Tasks (`Tasks.vue`) can be linked to Leads/Deals, assigned to team members, given due dates, and tracked in a unified list or Calendar (`Calendar.vue`).
- **Notes**: Collaborative rich-text notes attached directly to record activity feeds (`Notes.vue`).
- **Call Logs**: Native call logging (`CallLogs.vue`) records duration, call disposition, notes, and user timestamps for agent productivity auditing.

---

## 3. Dynamic Form Scripting Engine

Frappe CRM includes a runtime form scripting engine allowing client-side customization without frontend re-builds:

- **Record Storage**: Form scripts are defined as `CRM Form Script` records in Frappe Python.
- **Runtime Evaluation**: `frontend/src/data/script.js` evaluates class strings using `new Function(...)` in the browser sandbox.
- **Triggers & Controls**:
  - `onload`, `refresh`, `validate`, `before_save`, `after_save`
  - Field-level mutations via `setFieldProperty(fieldname, property, value)` (e.g., hidden, read_only, mandatory, options).
  - Standalone modal popups using `formDialog()`.

---

## 4. Tech Stack & Directory Structure

```
crm/
├── crm/                    # Frappe Python App (Backend)
│   ├── api/                # Custom REST API endpoints (whatsapp, doc, dashboard, etc.)
│   ├── demo/               # Demo data generators (whatsapp_demo, etc.)
│   ├── fcrm/doctype/       # DocType schemas (crm_lead, crm_deal, crm_form_script, etc.)
│   ├── hooks.py            # Frappe hooks (events, doctype overrides, webhooks)
│   └── patches/            # DB migrations & layout updates
│
├── frontend/               # Vue 3 Frontend (Single Page App)
│   ├── src/
│   │   ├── components/     # Reusable UI components & Modals
│   │   ├── data/           # Document stores & script execution engine (`document.js`, `script.js`)
│   │   ├── pages/          # Application views (Leads, Deals, WhatsAppInbox, Calendar, etc.)
│   │   ├── router.js       # SPA client routes
│   │   └── utils/          # Helper utilities & pure logic (`scriptHelpers.js`, `whatsappInbox.js`)
│   └── tests/              # Unit tests (Vitest)
│
└── docker/                 # Local & production container orchestration
    ├── docker-compose.local.yml
    └── init-local.sh
```

---

## 5. Local Development Workflow

To run the full stack locally:

```bash
# 1. Backend (Frappe Bench via Docker)
docker compose -f docker/docker-compose.local.yml up -d

# 2. Frontend (Vite Dev Server)
cd frontend
yarn install
yarn dev
```

- **Frontend App**: `http://localhost:8080` (API calls automatically proxied to `:8000`)
- **Backend Bench**: `http://localhost:8000`
- **Unit Verification**: `cd frontend && yarn test:run`
