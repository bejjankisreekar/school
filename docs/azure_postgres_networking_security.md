# Azure PostgreSQL networking & security checklist

This project reads DB connectivity from `.env` (`DB_HOST`, `DB_PORT`, etc.). For production, prefer private networking and managed secrets.

## 1) Choose connectivity model

### Recommended: Private access (production-grade)
Use private networking so your app connects to the database without exposing it to the internet.

Checklist:
- Place the Flexible Server in (or integrated with) a **VNet** reachable by your app environment.
- Configure **Private DNS** so the server hostname resolves to a private IP from your app network.
- Verify name resolution from the app hosts/containers.

### Temporary fallback: Public access (strictly controlled)
If private access is not feasible immediately:
- Enable public access but **restrict firewall** to an IP allowlist:
  - app hosting outbound IPs
  - the migration runner/DMS connectivity IPs
- Keep the allowlist short and remove entries after migration.

## 2) TLS/SSL requirements

Checklist:
- Enforce **SSL required** on Azure PostgreSQL.
- Ensure the app runtime trusts the Azure PostgreSQL CA chain.
- Confirm your DB client settings use TLS (Django/psycopg2 typically supports this via connection options).

## 3) Identity, roles, and least privilege

Checklist:
- Create an **app runtime** DB user with only the permissions needed by the application.
- Use a separate **migration** user for the migration window only.
- Avoid using the server admin user for application traffic.

## 4) Secrets management

Checklist:
- Store production DB credentials in **Azure Key Vault** (or your platform’s secret store).
- Rotate credentials after migration/cutover.
- Ensure `.env` is never committed; if secrets were committed previously, treat them as compromised and rotate them.

## 5) Operational logging and monitoring (minimum baseline)

Checklist:
- Enable logs needed for troubleshooting:
  - connection/auth failures
  - slow query logging (with a sensible threshold)
- Add alerts for:
  - high CPU
  - storage nearing limit
  - high connection counts
  - frequent failed connections

