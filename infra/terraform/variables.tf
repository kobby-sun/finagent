variable "project" {
  type    = string
  default = "finagent"
}
variable "environment" {
  type    = string
  default = "poc"
}
variable "region" {
  type    = string
  default = "ap-southeast-2"
}
variable "tags" {
  type    = map(string)
  default = {}
}
variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}
variable "allowed_cidrs" {
  description = "CIDRs allowed to reach the ALB. Lock this down (office/VPN). Never 0.0.0.0/0 for real data."
  type        = list(string)
}
variable "image_tag" {
  type    = string
  default = "latest"
}
variable "cpu" {
  type    = number
  default = 512
}
variable "memory" {
  type    = number
  default = 1024
}
variable "llm_provider" {
  description = "mock | bedrock | anthropic | ollama"
  type        = string
  default     = "bedrock"
}
variable "bedrock_model_id" {
  type    = string
  default = "anthropic.claude-opus-5-5"
}
variable "acm_certificate_arn" {
  description = "Optional ACM cert for HTTPS on the ALB. Empty = HTTP only (POC)."
  type        = string
  default     = ""
}
variable "log_retention_days" {
  type    = number
  default = 30
}
variable "enable_webhook_retry_schedule" {
  type    = bool
  default = true
}
variable "fixed_now" {
  description = "Freeze the clock to the mock-data date for demos; set to \"null\" for wall-clock time."
  type        = string
  default     = "2026-11-12T10:00:00+11:00"
}
