# Generated secrets - values never appear in code or tfvars. Rotate with `terraform apply -replace=...`.
resource "random_password" "webhook" {
  length  = 48
  special = false
}

resource "random_password" "api_key" {
  length  = 40
  special = false
}

resource "aws_secretsmanager_secret" "webhook" {
  name_prefix             = "${local.name}/webhook-signing-"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "webhook" {
  secret_id     = aws_secretsmanager_secret.webhook.id
  secret_string = random_password.webhook.result
}

resource "aws_secretsmanager_secret" "api_key" {
  name_prefix             = "${local.name}/api-key-"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "api_key" {
  secret_id     = aws_secretsmanager_secret.api_key.id
  secret_string = random_password.api_key.result
}

# Optional: Anthropic API key (only if llm_provider = "anthropic"). Set the value out-of-band:
#   aws secretsmanager put-secret-value --secret-id <arn> --secret-string "$ANTHROPIC_API_KEY"
resource "aws_secretsmanager_secret" "anthropic" {
  count                   = var.llm_provider == "anthropic" ? 1 : 0
  name_prefix             = "${local.name}/anthropic-api-key-"
  recovery_window_in_days = 0
}
