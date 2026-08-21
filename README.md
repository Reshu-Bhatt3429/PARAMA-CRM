# PARAMA CRM

PARAMA CRM is a travel-sales workspace for leads, deals, customer communication,
WhatsApp conversations, itineraries, invoicing, tasks, and follow-ups.

## Main capabilities

- Lead, deal, contact, and organization management
- Unified WhatsApp inbox through the companion WhatsApp application
- AI-assisted, editable travel itineraries and printable proposals
- GST invoicing, payment tracking, and PDF output
- Tasks, notes, calendar, email, Twilio, and Exotel integrations
- Dashboards, assignment rules, workflow rules, SLA policies, and custom forms

## Runtime

This application currently targets the Frappe 17 development line and Vue 3.
The tested framework commit and companion WhatsApp commit are pinned in the
Docker configuration so fresh developer environments are reproducible.

## Run the existing local Bench

Install the frontend packages once:

```bash
cd frontend
yarn install --frozen-lockfile
cd ..
```

Then start the backend and frontend together:

```bash
yarn dev:stack
```

Open `http://crm.localhost:8080/crm`. The script binds the frontend to
`127.0.0.1` by default. Set `CRM_BENCH_DIR` only if the Bench directory is not
the sibling `crm-bench` directory.

## Run the isolated Docker development stack

Docker is for local development only; it is not the internet-facing production
topology. Create local secrets and start the stack:

```bash
cp .env.example .env
# Replace both placeholder values in .env with long random passwords.
docker compose --env-file .env -f docker/docker-compose.local.yml up -d
```

Open `http://crm.localhost:8000/crm` and sign in as `Administrator` with the
password stored in `CRM_ADMIN_PASSWORD`. Database, queue, and Bench data live in
separate named volumes. Both exposed ports bind to loopback only.

## Test and build

```bash
cd frontend
yarn test:run
yarn eslint src tests/unit --max-warnings=0
yarn build
cd ..
ruff check crm
```

## Production deployment

Read [docs/production-readiness.md](docs/production-readiness.md), then run:

```bash
scripts/harden-production.sh your-crm.example.com
```

The production script tests and builds the frontend, backs up the database and
files, migrates the site, applies secure configuration flags, and runs the
application readiness checks before a process restart.
