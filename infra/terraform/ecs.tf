resource "aws_ecs_cluster" "this" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

locals {
  image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  environment = [
    { name = "FINAGENT__APP__ENV", value = var.environment },
    { name = "FINAGENT__APP__DATA_DIR", value = "/data" },
    { name = "FINAGENT__APP__FIXED_NOW", value = var.fixed_now },
    { name = "FINAGENT__LLM__PROVIDER", value = var.llm_provider },
    { name = "FINAGENT__LLM__PROVIDERS__BEDROCK__REGION", value = var.region },
    { name = "FINAGENT__LLM__PROVIDERS__BEDROCK__MODEL", value = var.bedrock_model_id },
    { name = "FINAGENT__WEBHOOKS__DELIVER_INLINE", value = "true" },
  ]
  secrets = concat([
    { name = "FINAGENT__WEBHOOKS__SIGNING_SECRET", valueFrom = aws_secretsmanager_secret.webhook.arn },
    { name = "FINAGENT__APP__API_KEY", valueFrom = aws_secretsmanager_secret.api_key.arn },
  ], [for s in aws_secretsmanager_secret.anthropic : { name = "ANTHROPIC_API_KEY", valueFrom = s.arn }])
  container = {
    name                   = "finagent"
    image                  = local.image
    essential              = true
    portMappings           = [{ containerPort = 8080, protocol = "tcp" }]
    environment            = local.environment
    secrets                = local.secrets
    mountPoints            = [{ sourceVolume = "data", containerPath = "/data", readOnly = false }]
    readonlyRootFilesystem = false
    logConfiguration = {
      logDriver = "awslogs"
      options   = { "awslogs-group" = aws_cloudwatch_log_group.app.name, "awslogs-region" = var.region, "awslogs-stream-prefix" = "api" }
    }
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume {
    name = "data"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.data.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.data.id
        iam             = "ENABLED"
      }
    }
  }
  container_definitions = jsonencode([local.container])
}

resource "aws_ecs_service" "app" {
  name                               = local.name
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.app.arn
  desired_count                      = 1 # SQLite = single writer. Scale out only after moving to Postgres/DynamoDB.
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 0 # never two writers on the same SQLite file
  deployment_maximum_percent         = 100
  enable_execute_command             = true
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "finagent"
    container_port   = 8080
  }
  depends_on = [aws_lb_listener.http, aws_efs_mount_target.data]
}
