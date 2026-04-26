# Provisioning Azure Database for PostgreSQL (Flexible Server)

Use this as a practical checklist for creating the target database server in Azure.

## 1) Create the server

In Azure Portal:
- Create resource: **Azure Database for PostgreSQL flexible server**
- **Subscription / Resource group**: choose your production subscription and an RG like `rg-school-erp-prod`
- **Server name**: choose a stable name (this becomes part of the hostname)
- **Region**: same as your app hosting region
- **PostgreSQL version**: match your source major version when possible (to reduce migration risk)

## 2) Compute and storage

Starting guidance (adjust based on your inventory):
- **Workload tier**: General Purpose
- **vCores / RAM**: pick a conservative baseline; scale up after cutover if CPU becomes hot
- **Storage**: provision about 1.5×–2× current DB size

## 3) Availability, backups, and maintenance

- **High availability**: enable zone-redundant HA if you need production uptime
- **Backups**:
  - retention: choose a window that matches your ops (commonly 7–35 days)
  - ensure you understand point-in-time restore (PITR) behavior
- **Maintenance window**: choose a quiet period to reduce impact of planned maintenance

## 4) Security defaults

- Require **TLS/SSL** connections
- Plan to store app DB credentials in **Azure Key Vault** (or your hosting secret store)
- Create separate users:
  - **migration user** (temporary, elevated)
  - **app runtime user** (least privilege)

## 5) Database + roles checklist (conceptual)

Before cutover, ensure you have:
- Target database created (name should match what your app expects, or you plan to update `DB_NAME`)
- App runtime user created and granted required privileges
- Any required extensions enabled (only those supported on Azure)

## 6) Record these outputs (you’ll need them later)

- Server hostname (e.g., `yourserver.postgres.database.azure.com`)
- Admin username format (Azure often uses `user@servername`)
- Port (typically `5432`)
- TLS requirement and CA chain requirements

