# Application Load Balancer for FuzeInfra Services
# This creates a highly available load balancer with target groups for each service

# Data source to get VPC subnets for ALB placement
data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [local.default_vpc_id]
  }
  
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Application Load Balancer
resource "aws_lb" "fuzeinfra_alb" {
  name               = "fuzeinfra-alb-${var.environment}"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = data.aws_subnets.public.ids

  enable_deletion_protection = false
  
  # Access logs (optional)
  # access_logs {
  #   bucket  = aws_s3_bucket.fuzeinfra_artifacts[0].bucket
  #   prefix  = "alb-logs"
  #   enabled = var.create_artifacts_bucket
  # }

  tags = {
    Name        = "fuzeinfra-alb-${var.environment}"
    Environment = var.environment
    Purpose     = "Load balancer for FuzeInfra services"
  }
}

# Security group for ALB
resource "aws_security_group" "alb" {
  name_prefix = "fuzeinfra-alb-${var.environment}-"
  description = "Security group for FuzeInfra Application Load Balancer"
  vpc_id      = local.default_vpc_id

  # HTTP
  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # HTTPS
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # All outbound traffic to EC2 instances
  egress {
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  tags = {
    Name = "fuzeinfra-alb-${var.environment}"
  }

  lifecycle {
    create_before_destroy = true
  }
}

# Update EC2 security group to allow ALB traffic
resource "aws_security_group_rule" "alb_to_ec2" {
  type                     = "ingress"
  from_port                = 0
  to_port                  = 65535
  protocol                 = "tcp"
  security_group_id        = aws_security_group.fuzeinfra.id
  source_security_group_id = aws_security_group.alb.id
  description              = "Allow traffic from ALB to EC2 instances"
}

# Target Groups for each service

# Main Nginx (Default)
resource "aws_lb_target_group" "nginx" {
  name     = "fuzeinfra-nginx-${var.environment}"
  port     = 80
  protocol = "HTTP"
  vpc_id   = local.default_vpc_id
  
  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
    path                = "/health"
    matcher             = "200"
    protocol            = "HTTP"
    port                = "traffic-port"
  }

  tags = {
    Name = "fuzeinfra-nginx-${var.environment}"
  }
}

# Grafana Target Group
resource "aws_lb_target_group" "grafana" {
  name     = "fuzeinfra-grafana-${var.environment}"
  port     = 3000
  protocol = "HTTP"
  vpc_id   = local.default_vpc_id
  
  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
    path                = "/api/health"
    matcher             = "200"
    protocol            = "HTTP"
    port                = "traffic-port"
  }

  tags = {
    Name = "fuzeinfra-grafana-${var.environment}"
  }
}

# Prometheus Target Group
resource "aws_lb_target_group" "prometheus" {
  name     = "fuzeinfra-prometheus-${var.environment}"
  port     = 9090
  protocol = "HTTP"
  vpc_id   = local.default_vpc_id
  
  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
    path                = "/-/healthy"
    matcher             = "200"
    protocol            = "HTTP"
    port                = "traffic-port"
  }

  tags = {
    Name = "fuzeinfra-prometheus-${var.environment}"
  }
}

# Health Dashboard Target Group
resource "aws_lb_target_group" "health_dashboard" {
  name     = "fuzeinfra-health-${var.environment}"
  port     = 8082
  protocol = "HTTP"
  vpc_id   = local.default_vpc_id
  
  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
    path                = "/health"
    matcher             = "200"
    protocol            = "HTTP"
    port                = "traffic-port"
  }

  tags = {
    Name = "fuzeinfra-health-${var.environment}"
  }
}

# Airflow Target Group
resource "aws_lb_target_group" "airflow" {
  name     = "fuzeinfra-airflow-${var.environment}"
  port     = 8080
  protocol = "HTTP"
  vpc_id   = local.default_vpc_id
  
  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
    path                = "/health"
    matcher             = "200"
    protocol            = "HTTP"
    port                = "traffic-port"
  }

  tags = {
    Name = "fuzeinfra-airflow-${var.environment}"
  }
}

# MongoDB Express Target Group
resource "aws_lb_target_group" "mongo_express" {
  name     = "fuzeinfra-mongo-${var.environment}"
  port     = 8081
  protocol = "HTTP"
  vpc_id   = local.default_vpc_id
  
  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 10
    interval            = 30
    path                = "/"
    matcher             = "200"
    protocol            = "HTTP"
    port                = "traffic-port"
  }

  tags = {
    Name = "fuzeinfra-mongo-${var.environment}"
  }
}

# RabbitMQ Management Target Group
resource "aws_lb_target_group" "rabbitmq" {
  name     = "fuzeinfra-rabbitmq-${var.environment}"
  port     = 15672
  protocol = "HTTP"
  vpc_id   = local.default_vpc_id
  
  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 10
    interval            = 30
    path                = "/"
    matcher             = "200"
    protocol            = "HTTP"
    port                = "traffic-port"
  }

  tags = {
    Name = "fuzeinfra-rabbitmq-${var.environment}"
  }
}

# Attach EC2 instance to target groups
resource "aws_lb_target_group_attachment" "nginx" {
  target_group_arn = aws_lb_target_group.nginx.arn
  target_id        = local.instance_id
  port             = 80
}

resource "aws_lb_target_group_attachment" "grafana" {
  target_group_arn = aws_lb_target_group.grafana.arn
  target_id        = local.instance_id
  port             = 3000
}

resource "aws_lb_target_group_attachment" "prometheus" {
  target_group_arn = aws_lb_target_group.prometheus.arn
  target_id        = local.instance_id
  port             = 9090
}

resource "aws_lb_target_group_attachment" "health_dashboard" {
  target_group_arn = aws_lb_target_group.health_dashboard.arn
  target_id        = local.instance_id
  port             = 8082
}

resource "aws_lb_target_group_attachment" "airflow" {
  target_group_arn = aws_lb_target_group.airflow.arn
  target_id        = local.instance_id
  port             = 8080
}

resource "aws_lb_target_group_attachment" "mongo_express" {
  target_group_arn = aws_lb_target_group.mongo_express.arn
  target_id        = local.instance_id
  port             = 8081
}

resource "aws_lb_target_group_attachment" "rabbitmq" {
  target_group_arn = aws_lb_target_group.rabbitmq.arn
  target_id        = local.instance_id
  port             = 15672
}

# ALB Listeners and Rules

# HTTP Listener (redirects to HTTPS)
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.fuzeinfra_alb.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# HTTPS Listener (main routing)
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.fuzeinfra_alb.arn
  port              = "443"
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS-1-2-2017-01"
  certificate_arn   = aws_acm_certificate_validation.fuzeinfra.certificate_arn

  # Default action - route to nginx
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.nginx.arn
  }
}

# SSL Certificate for ALB
resource "aws_acm_certificate" "fuzeinfra" {
  domain_name               = "infra.${var.domain_name}"
  subject_alternative_names = [
    "*.infra.${var.domain_name}"
  ]
  validation_method = "DNS"

  lifecycle {
    create_before_destroy = true
  }

  tags = {
    Name = "fuzeinfra-${var.environment}"
  }
}

# Certificate validation records
resource "cloudflare_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.fuzeinfra.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id = data.cloudflare_zones.fuzefront.zones[0].id
  name    = each.value.name
  content = each.value.record
  type    = each.value.type
  ttl     = 60
}

resource "aws_acm_certificate_validation" "fuzeinfra" {
  certificate_arn         = aws_acm_certificate.fuzeinfra.arn
  validation_record_fqdns = [for record in cloudflare_record.cert_validation : record.hostname]
}

# Listener Rules for hostname-based routing

# Grafana
resource "aws_lb_listener_rule" "grafana" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.grafana.arn
  }

  condition {
    host_header {
      values = ["grafana.infra.${var.domain_name}"]
    }
  }
}

# Prometheus
resource "aws_lb_listener_rule" "prometheus" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 110

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.prometheus.arn
  }

  condition {
    host_header {
      values = ["prometheus.infra.${var.domain_name}"]
    }
  }
}

# Health Dashboard (ArgoCD endpoint)
resource "aws_lb_listener_rule" "health_dashboard" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 120

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.health_dashboard.arn
  }

  condition {
    host_header {
      values = ["argocd.infra.${var.domain_name}"]
    }
  }
}

# Airflow
resource "aws_lb_listener_rule" "airflow" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 130

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.airflow.arn
  }

  condition {
    host_header {
      values = ["airflow.infra.${var.domain_name}"]
    }
  }
}

# MongoDB Express
resource "aws_lb_listener_rule" "mongo_express" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 140

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.mongo_express.arn
  }

  condition {
    host_header {
      values = ["mongo.infra.${var.domain_name}"]
    }
  }
}

# RabbitMQ Management
resource "aws_lb_listener_rule" "rabbitmq" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 150

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.rabbitmq.arn
  }

  condition {
    host_header {
      values = ["rabbitmq.infra.${var.domain_name}"]
    }
  }
}

# Outputs
output "alb_info" {
  description = "Application Load Balancer information"
  value = {
    alb_dns_name       = aws_lb.fuzeinfra_alb.dns_name
    alb_zone_id        = aws_lb.fuzeinfra_alb.zone_id
    alb_arn            = aws_lb.fuzeinfra_alb.arn
    certificate_arn    = aws_acm_certificate.fuzeinfra.arn
    target_groups = {
      nginx           = aws_lb_target_group.nginx.arn
      grafana         = aws_lb_target_group.grafana.arn
      prometheus      = aws_lb_target_group.prometheus.arn
      health_dashboard = aws_lb_target_group.health_dashboard.arn
      airflow         = aws_lb_target_group.airflow.arn
      mongo_express   = aws_lb_target_group.mongo_express.arn
      rabbitmq        = aws_lb_target_group.rabbitmq.arn
    }
  }
}