# Terraform

This directory is reserved for deployment infrastructure. The application
stack is intentionally cloud-neutral; select a cloud provider, deployment
target, network model, and secret manager before adding provider resources.

Keep state remote and encrypted, manage secrets outside Terraform state, and
provision one database per tenant when using database-per-tenant isolation.
PgBouncer can be placed in front of PostgreSQL where connection counts justify
it. Do not commit state files or production variable files.

