output "api_url" {
  value = "${var.acm_certificate_arn == "" ? "http" : "https"}://${aws_lb.this.dns_name}"
}
output "swagger_url" {
  value = "${var.acm_certificate_arn == "" ? "http" : "https"}://${aws_lb.this.dns_name}/docs"
}
output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}
output "ecs_cluster" {
  value = aws_ecs_cluster.this.name
}
output "ecs_service" {
  value = aws_ecs_service.app.name
}
output "corpus_bucket" {
  value = aws_s3_bucket.corpus.bucket
}
output "api_key_secret_arn" {
  value = aws_secretsmanager_secret.api_key.arn
}
output "webhook_secret_arn" {
  value = aws_secretsmanager_secret.webhook.arn
}
output "log_group" {
  value = aws_cloudwatch_log_group.app.name
}
