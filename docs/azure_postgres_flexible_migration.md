# Azure Database for PostgreSQL (Flexible Server) migration runbook (near-zero downtime)

This project uses **PostgreSQL** (via `django-tenants`) and reads connection settings from `.env`:

- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
- Source of truth: `school_erp_demo/settings.py`

This runbook focuses on moving the database to **Azure Database for PostgreSQL (Flexible Server)** with **minimal downtime** using **online migration** (continuous replication) and a short cutover window.

## 0) Important safety notes

### Secrets hygiene
- **Do not commit** real secrets to git. The repo’s `.gitignore` already ignores `.env`, but if a secret was ever committed, treat it as leaked and rotate it.
- Use **Azure Key Vault** (or your hosting platform’s secret store) for production DB credentials.

### Multi-tenancy (django-tenants)
This app uses `django-tenants` and stores tenant data in **separate schemas** within the same PostgreSQL database. Any migration must preserve:
- the **public schema** (shared apps)
- all **tenant schemas**
- roles/permissions as needed

## 1) Inventory checklist (fill this in before provisioning)

Collect these from the **source PostgreSQL**.

### A. Version, size, and top tables
Run (from a machine that can reach the DB):

```sql
SELECT version();
SELECT current_database() AS db, pg_size_pretty(pg_database_size(current_database())) AS db_size;

-- Largest tables (across schemas)
SELECT
  n.nspname AS schema,
  c.relname AS table,
  pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 25;
```

### B. Extensions in use

```sql
SELECT extname, extversion FROM pg_extension ORDER BY extname;
```

Confirm each extension is supported on Azure PostgreSQL Flexible Server (PostGIS, if used, must be planned explicitly).

### C. Replication feasibility checks (for online migration)

```sql
SHOW wal_level;
SHOW max_wal_senders;
SHOW max_replication_slots;
SHOW max_worker_processes;
```

Also identify long-running transactions that can block replication/cutover:

```sql
SELECT pid, usename, state, now() - xact_start AS xact_age, query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
ORDER BY xact_age DESC
LIMIT 20;
```

### D. Non-app DB clients (must be cut over too)
List anything that connects directly to Postgres besides the Django app:
- BI/reporting tools
- scheduled jobs / cron / Windows Task Scheduler
- admin tools
- background workers and separate services

## 2) Provision Azure Database for PostgreSQL (Flexible Server)

### A. Pick region and HA
- Choose the **same region** as your app hosting to reduce latency.
- Enable **zone-redundant HA** if you need high uptime (recommended for production ERP).

### B. Sizing guidance (starting point)
- **Compute**: start with General Purpose; scale up if CPU becomes a bottleneck.
- **Storage**: provision ~\(1.5×–2×\) current DB size to allow growth and migration overhead.
- **Connections**: confirm expected peak connections; plan for pooling if needed.

### C. Create roles
Create (conceptually) two users:
- **Migration user**: temporary, elevated, used by the migration tool.
- **App user**: least privilege for day-to-day operations.

## 3) Networking and security (recommended production setup)

### A. Connectivity model
Prefer **Private access**:
- Azure VNet integration for the Flexible Server
- Private DNS zone so your app resolves the server privately

If you must use public access temporarily:
- Strict **IP allowlist** (only your app hosts + migration runner)
- Require TLS/SSL

### B. TLS
- Enforce **SSL required** on Azure PostgreSQL.
- Ensure your app runtime trusts the CA chain used by Azure PostgreSQL.

### C. Secrets
- Store DB secrets in **Key Vault** (or equivalent).
- Rotate secrets after migration.

## 4) Online migration (near-zero downtime)

Recommended tool: **Azure Database Migration Service (DMS)** using an **online migration** method.

### A. Pre-migration preparation
- Ensure source DB is healthy (vacuum/analyze as appropriate).
- Ensure networking allows the migration service/runner to reach **both** source and Azure target.

### B. Initial load
The online workflow will perform a bulk copy of existing schema + data to Azure.

### C. Continuous replication
After the initial load, keep replication running until lag is consistently near-zero.
Monitor:
- replication lag
- errors related to unsupported objects/extensions/permissions
- performance impact on source DB

## 5) Cutover (minutes)

### A. Freeze writes briefly
To guarantee consistency at cutover:
- Put the app in **maintenance mode** (or otherwise block writes)
- Pause any scheduled jobs that write to DB

### B. Final sync + switch endpoints
- Wait for replication to reach **0 lag**
- Complete the migration tool’s **cutover** step
- Switch the application’s DB endpoint to Azure (update the secret value used for `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`)

## 6) Post-cutover validation (first hour)

Run a quick smoke test:
- login
- create/update records
- billing/fee flows
- background jobs

Observe:
- connection counts
- CPU/IO
- slow queries
- error logs

## 7) Rollback strategy (keep it simple)

For a defined window (e.g., 24–72 hours):
- Keep the **source DB unchanged**
- If a critical issue occurs, switch the app endpoint back to source

Only decommission the source after stability is proven and backups/restores are verified on Azure.

