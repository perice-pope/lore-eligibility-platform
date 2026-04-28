variable "partner_ids" { type = list(string) }
variable "landing_buckets" { type = map(string) }
# Stub — AWS Transfer Family SFTP per partner. See ../README.md.
output "sftp_endpoints" { value = { for p in var.partner_ids : p => "sftp.${p}.lore.co" } }
