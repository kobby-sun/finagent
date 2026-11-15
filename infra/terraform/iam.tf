data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name_prefix        = "${local.name}-exec-"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat([aws_secretsmanager_secret.webhook.arn, aws_secretsmanager_secret.api_key.arn],
    aws_secretsmanager_secret.anthropic[*].arn)
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# Task role = what the application itself may do. Least privilege: invoke the configured model, read the corpus
# bucket, use its EFS access point. No permission exists that could move money (posting is simulated).
resource "aws_iam_role" "task" {
  name_prefix        = "${local.name}-task-"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    sid       = "InvokeConfiguredModelOnly"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream", "bedrock-mantle:*"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/${var.bedrock_model_id}", "arn:aws:bedrock:${var.region}:*:inference-profile/*"]
  }
  statement {
    sid       = "ReadCorpus"
    actions   = ["s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.corpus.arn, "${aws_s3_bucket.corpus.arn}/*"]
  }
  statement {
    sid       = "EfsAccessPoint"
    actions   = ["elasticfilesystem:ClientMount", "elasticfilesystem:ClientWrite"]
    resources = [aws_efs_file_system.data.arn]
    condition {
      test     = "StringEquals"
      variable = "elasticfilesystem:AccessPointArn"
      values   = [aws_efs_access_point.data.arn]
    }
  }
}

resource "aws_iam_role_policy" "task" {
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}
