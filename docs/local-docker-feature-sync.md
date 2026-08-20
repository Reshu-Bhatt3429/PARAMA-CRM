# Updating an Existing Local Docker Site

Use this guide when one developer can see features such as WhatsApp,
Itineraries, Invoices, workflow rules, or reminders, but another developer who
pulled the same branch cannot.

## Why `git pull` is not enough

The local Docker stack has two independent copies of state:

1. The host checkout contains the Git branch.
2. The `crm-local-frappe-1` container contains a separate clone at
   `/home/frappe/frappe-bench/apps/crm` and a persistent Frappe site database.

When the bench already exists, `docker/init-local.sh` starts it without cloning
the host repository again. Consequently, a host `git pull` does not update the
container clone, run Frappe migrations, rebuild frontend assets, install an
optional app, or copy settings and records from another developer's database.

The repository includes `docker/deploy-local.sh` to perform the code, schema,
asset, cache, and restart steps safely. It takes a database backup before
running `bench migrate`.

## 1. Update the host checkout

From the repository root:

```bash
git switch main
git pull --ff-only origin main
git submodule update --init --recursive
```

## 2. Start the local stack

```bash
docker-compose -f docker/docker-compose.local.yml up -d
```

If Docker was installed with the Compose plugin, use `docker compose` instead
of `docker-compose`.

## 3. Deploy the committed host state into the bench

```bash
bash docker/deploy-local.sh
```

The deploy script:

- fetches the current host branch into the container's CRM clone;
- resets that clone to the committed host revision;
- backs up the site database;
- runs `bench migrate` to create or update DocTypes and fields;
- installs the locked frontend dependencies and rebuilds CRM assets;
- clears the site cache; and
- restarts the Frappe container.

Only committed files are deployed. If the script reports host changes, commit
them first. If it reports tracked changes inside the container, it writes a
rescue patch and refuses to overwrite them. Review that patch before following
its `--discard-container-changes` instruction.

Verify that the host and container now serve the same commit:

```bash
git rev-parse --short HEAD

docker exec crm-local-frappe-1 \
  git -C /home/frappe/frappe-bench/apps/crm rev-parse --short HEAD
```

The two hashes should match.

## 4. Verify the WhatsApp dependency

WhatsApp is supplied by the separate `frappe_whatsapp` app. Check the apps
installed on this site:

```bash
docker exec -w /home/frappe/frappe-bench \
  crm-local-frappe-1 \
  bench --site crm.localhost list-apps
```

The output must contain both `crm` and `frappe_whatsapp`. If
`frappe_whatsapp` is absent, install it:

```bash
docker exec -w /home/frappe/frappe-bench \
  crm-local-frappe-1 \
  bench get-app https://github.com/shridarpatil/frappe_whatsapp.git

docker exec -w /home/frappe/frappe-bench \
  crm-local-frappe-1 \
  bench --site crm.localhost install-app frappe_whatsapp

docker exec -w /home/frappe/frappe-bench \
  crm-local-frappe-1 \
  bench --site crm.localhost clear-cache

docker restart crm-local-frappe-1
```

Run `bench get-app` only when `apps/frappe_whatsapp` is not already present in
the bench. If the app directory exists but `list-apps` does not list it, skip
`bench get-app` and run only `install-app`, `clear-cache`, and the restart.

Installation makes the WhatsApp section visible. Sending and receiving also
require an active WhatsApp Account and that account must be selected as the
default outgoing account in WhatsApp Settings. Those credentials and records
live in the site database and are not transferred through Git.

## 5. Enable site-level feature flags

The expansion features intentionally default to off on every new site. Log in
as `Administrator` or a System Manager, then open:

**CRM → Settings → General → Feature Flags**

Enable the features required on that site:

- Outbound engine
- Email sequences
- Task due-date reminders
- Deal health flags
- Workflow rules
- Invoices
- Invoice payment reminders

Invoice payment reminders also require both **Invoices** and **Outbound
engine**. The Invoices sidebar entry and Deal invoice action remain hidden
while **Invoices** is off.

The current flag values can be inspected from the command line:

```bash
docker exec -w /home/frappe/frappe-bench \
  crm-local-frappe-1 \
  bench --site crm.localhost execute crm.feature_flags.all_flags
```

## 6. Check the signed-in user's role

Some navigation is permission-aware. Itineraries require a Sales User, Sales
Manager, or System Manager role plus read permission on CRM Itinerary. Settings
are desktop-only and require the appropriate management permissions. When
testing a newly deployed site, first sign in as `Administrator` to distinguish
a deployment problem from an ordinary role restriction.

## 7. Understand what Git does not copy

Each developer has a different MariaDB volume. The following are local site
data and do not arrive in a pull request:

- enabled feature flags;
- WhatsApp Account credentials and defaults;
- users, roles, and permissions assigned after installation;
- leads, deals, invoices, messages, and other records; and
- demo data.

For a disposable local demo site, the standard CRM demo data can be created
with:

```bash
docker exec -w /home/frappe/frappe-bench \
  crm-local-frappe-1 \
  bench --site crm.localhost execute crm.demo.api.create_demo_data
```

After `frappe_whatsapp` is installed, synthetic WhatsApp inbox data can be
created with:

```bash
docker exec -w /home/frappe/frappe-bench \
  crm-local-frappe-1 \
  bench --site crm.localhost execute crm.demo.whatsapp_demo.seed
```

The WhatsApp seeder is for throwaway local demo sites. It creates synthetic
records and does not configure a real Meta integration.

## Quick diagnosis

If a feature is still missing, collect these four outputs:

```bash
git rev-parse --short HEAD

docker exec crm-local-frappe-1 \
  git -C /home/frappe/frappe-bench/apps/crm rev-parse --short HEAD

docker exec -w /home/frappe/frappe-bench \
  crm-local-frappe-1 \
  bench --site crm.localhost list-apps

docker exec -w /home/frappe/frappe-bench \
  crm-local-frappe-1 \
  bench --site crm.localhost execute crm.feature_flags.all_flags
```

Mismatched commit hashes mean the deploy script has not completed. A missing
`frappe_whatsapp` entry means the dependency is not installed. A `false` flag
means that feature is correctly hidden until a manager enables it.
