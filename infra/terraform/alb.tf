resource "aws_lb" "this" {
  name                       = substr(local.name, 0, 32)
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "app" {
  name        = substr("${local.name}-tg", 0, 32)
  port        = 8080
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id
  health_check {
    path    = "/healthz"
    matcher = "200"
  }
  deregistration_delay = 10
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = var.acm_certificate_arn == "" ? 80 : 443
  protocol          = var.acm_certificate_arn == "" ? "HTTP" : "HTTPS"
  certificate_arn   = var.acm_certificate_arn == "" ? null : var.acm_certificate_arn
  ssl_policy        = var.acm_certificate_arn == "" ? null : "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
