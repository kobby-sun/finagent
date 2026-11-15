# Retries failed/pending webhook deliveries every 5 minutes (transactional outbox drain) and resumes any run left
# RUNNING by a crashed task. Both commands are idempotent.
resource "aws_iam_role" "scheduler" {
  count       = var.enable_webhook_retry_schedule ? 1 : 0
  name_prefix = "${local.name}-sched-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole",
  Principal = { Service = "scheduler.amazonaws.com" } }] })
}

resource "aws_iam_role_policy" "scheduler" {
  count = var.enable_webhook_retry_schedule ? 1 : 0
  role  = aws_iam_role.scheduler[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "ecs:RunTask", Resource = aws_ecs_task_definition.app.arn },
    { Effect = "Allow", Action = "iam:PassRole", Resource = [aws_iam_role.execution.arn, aws_iam_role.task.arn] }
  ] })
}

resource "aws_scheduler_schedule" "maintenance" {
  count               = var.enable_webhook_retry_schedule ? 1 : 0
  name                = "${local.name}-maintenance"
  schedule_expression = "rate(5 minutes)"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_ecs_cluster.this.arn
    role_arn = aws_iam_role.scheduler[0].arn
    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.app.arn
      launch_type         = "FARGATE"
      network_configuration {
        subnets          = aws_subnet.public[*].id
        security_groups  = [aws_security_group.task.id]
        assign_public_ip = true
      }
    }
    input = jsonencode({ containerOverrides = [{ name = "finagent",
    command = ["sh", "-c", "finagent webhooks deliver && finagent resume --all"] }] })
  }
}
