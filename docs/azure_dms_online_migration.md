# Azure Database Migration Service (DMS) — online migration (PostgreSQL → Azure PostgreSQL Flexible Server)

This guide assumes you want **near-zero downtime** via **online migration** (initial load + continuous replication + cutover).

## 1) When to use DMS online migration

Use this approach when:
- you can allow a short **write-freeze** window for final cutover (minutes)
- you want replication to keep Azure nearly in sync before cutover

## 2) Pre-requisites checklist

### A. Access and connectivity
- The migration service/runner must reach **source PostgreSQL**
- The migration service/runner must reach **Azure PostgreSQL Flexible Server**
- Firewalls / NSGs / allowlists must permit required connectivity

### B. Source database readiness
Before starting, record:
- `SELECT version();`
- `SELECT extname FROM pg_extension;`
- current DB size and largest tables

Ensure the source supports the needed replication settings for online migration (exact requirements vary by version and migration configuration).

## 3) DMS workflow (conceptual steps)

In Azure:
- Create or choose an instance of **Azure Database Migration Service**
- Create a **migration project** targeting *Azure Database for PostgreSQL Flexible Server*
- Choose **Online migration**

Then configure:
- **Source connection** (host, port, db, user, TLS)
- **Target connection** (Azure Flexible Server host, port, db, user, TLS)

## 4) Migration phases

### Phase 1: Initial load
- DMS copies schema and existing data to the Azure target
- Monitor throughput and errors

### Phase 2: Continuous replication
- DMS keeps applying ongoing changes from source → Azure
- Monitor replication lag and resolve any compatibility or permission issues

## 5) Monitoring checklist during replication

Watch for:
- increasing replication lag
- source DB load (CPU/IO)
- errors related to unsupported objects/extensions
- long-running transactions on source (can delay catch-up)

## 6) Cutover readiness checklist

You are ready to cut over when:
- replication lag is stable and low
- you have a confirmed short maintenance window for write-freeze
- app connectivity to Azure is already validated (DNS, firewall, TLS)
- rollback plan is prepared (see `docs/azure_cutover_rollback.md`)

