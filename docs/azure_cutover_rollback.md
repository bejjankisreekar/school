# Cutover + rollback checklist (Azure PostgreSQL Flexible Server)

This is the “do it live” checklist for switching production from source PostgreSQL to Azure PostgreSQL with a short write pause.

## 1) Pre-cutover (day before / hours before)

### A. Confirm replication health
- Replication is running and lag is consistently low.
- No unresolved migration errors.

### B. Verify app can connect to Azure DB
- DNS resolves correctly from the app environment.
- Firewall/private networking rules allow connectivity.
- TLS/SSL is working end-to-end.

### C. Freeze plan
- Identify all writers:
  - web app
  - background workers
  - scheduled jobs
  - external tools (reporting/BI)
- Decide the **cutover window** and notify stakeholders.

### D. Backups
- Ensure you have a recent verified backup/snapshot of the source DB.
- Confirm Azure backups/PITR is enabled on the target server.

## 2) Cutover (minutes)

### Step 1: Stop writes to source
- Put the app into maintenance mode (or otherwise block write endpoints).
- Stop background workers and scheduled jobs that write.

### Step 2: Wait for final sync
- Wait for replication lag to reach **0** (or the lowest achievable stable value).

### Step 3: Complete migration “cutover”
- Execute the migration tool’s cutover step.
- Ensure Azure is now the primary for the app.

### Step 4: Switch the app endpoint
- Update the production secret values that correspond to:
  - `DB_HOST` (Azure server hostname)
  - `DB_NAME`
  - `DB_USER`
  - `DB_PASSWORD`
  - `DB_PORT` (usually 5432)
- Restart/redeploy the app so it picks up the new secrets.

## 3) Post-cutover verification (first hour)

### A. Smoke tests
- Login
- Create/update key records
- Run billing/fees flows
- Check tenant switching (multiple schools/tenants)

### B. Observability
- Check error logs for DB connection/auth errors
- Watch CPU/IO and connection counts on Azure PostgreSQL
- Watch slow queries and timeouts

## 4) Rollback (keep source intact)

Keep the source DB unchanged for a defined rollback window (e.g., 24–72 hours).

Rollback trigger examples:
- widespread write failures
- severe performance regressions not quickly solvable
- data integrity issues found during validation

Rollback steps:
- Put the app back into maintenance mode
- Switch secrets/endpoint back to **source** DB
- Restart/redeploy app
- Confirm app is stable on source

## 5) Decommission (after stability window)

Only after stable operation on Azure and a restore test:
- remove public access/temporary allowlists (if used)
- reduce privileges/remove migration user
- document the final Azure server parameters
- plan source DB shutdown/deletion according to your retention policy

