locals {
  # Export/devtools serialize this file from the shared Python PRESETS contract.
  presets = jsondecode(file("${path.module}/presets.json"))
  size    = local.presets[var.preset]
  image_parts = try(regex("^(?P<account>[0-9]{12})\\.dkr\\.ecr\\.(?P<region>[a-z0-9-]+)\\.amazonaws\\.com/(?P<repository>[^@]+)@sha256:[a-f0-9]{64}$", var.image_uri), {
    account = "000000000000", region = var.aws_region, repository = "invalid-input"
  })
  repository_arn     = "arn:aws:ecr:${local.image_parts.region}:${local.image_parts.account}:repository/${local.image_parts.repository}"
  execution_role_arn = var.create_execution_role ? aws_iam_role.execution[0].arn : var.existing_execution_role_arn
  listener_ports     = var.exposure_mode == "https_existing_certificate" ? toset([80, 443]) : toset([80])
}

resource "aws_vpc" "app" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.project_name}-vpc" }
}
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.app.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.project_name}-public-${count.index + 1}" }
}
resource "aws_internet_gateway" "app" {
  vpc_id = aws_vpc.app.id
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.app.id
}
resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.app.id
}
resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}
resource "aws_security_group" "alb" {
  name_prefix = "${var.project_name}-alb-"
  description = "Reviewed client CIDRs to ALB only"
  vpc_id      = aws_vpc.app.id
}
resource "aws_security_group" "task" {
  name_prefix = "${var.project_name}-task-"
  description = "App ingress from ALB security group only"
  vpc_id      = aws_vpc.app.id
}
resource "aws_vpc_security_group_ingress_rule" "alb_clients" {
  for_each          = { for pair in setproduct(local.listener_ports, toset(var.allowed_cidrs)) : "${pair[0]}:${pair[1]}" => pair }
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = each.value[1]
  ip_protocol       = "tcp"
  from_port         = each.value[0]
  to_port           = each.value[0]
}
resource "aws_vpc_security_group_egress_rule" "alb_to_task" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
}
resource "aws_vpc_security_group_ingress_rule" "task_from_alb" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = var.container_port
  to_port                      = var.container_port
}
resource "aws_vpc_security_group_egress_rule" "task_https" {
  security_group_id = aws_security_group.task.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "HTTPS for ECR image pull and CloudWatch Logs; not destination isolation"
}
resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws-app-packager/${var.project_name}"
  retention_in_days = 7
}
resource "aws_iam_role" "execution" {
  count       = var.create_execution_role ? 1 : 0
  name_prefix = "${var.project_name}-exec-"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy" "execution" {
  count = var.create_execution_role ? 1 : 0
  name  = "pull-tested-image-and-write-app-logs"
  role  = aws_iam_role.execution[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = local.repository_arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.app.arn}:*" }
    ]
  })
}
resource "aws_ecs_cluster" "app" {
  name = var.project_name
}
resource "aws_ecs_task_definition" "app" {
  family                   = var.project_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = tostring(local.size.cpu)
  memory                   = tostring(local.size.memory)
  execution_role_arn       = local.execution_role_arn
  # No application task role is created. The image's ENTRYPOINT/CMD is preserved.
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume {
    name = "app-tmp"
  }
  # Platform 1.4.0 supplies 20 GiB task-scoped ephemeral storage by default.
  # Mount ownership/permissions require AWS verification for a non-root image.
  container_definitions = jsonencode([{
    name                   = "app"
    image                  = var.image_uri
    user                   = var.container_user
    essential              = true
    readonlyRootFilesystem = true
    cpu                    = local.size.cpu
    memory                 = local.size.memory
    portMappings           = [{ containerPort = var.container_port, hostPort = var.container_port, protocol = "tcp" }]
    environment            = [for key in sort(keys(var.environment)) : { name = key, value = var.environment[key] }]
    mountPoints            = [{ sourceVolume = "app-tmp", containerPath = "/tmp", readOnly = false }]
    linuxParameters        = { capabilities = { drop = ["ALL"] }, initProcessEnabled = true }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "app"
        "mode"                  = "non-blocking"
        "max-buffer-size"       = "4m"
      }
    }
  }])
}
resource "aws_lb" "app" {
  name                       = "${var.project_name}-alb"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [aws_security_group.alb.id]
  subnets                    = aws_subnet.public[*].id
  drop_invalid_header_fields = true
}
resource "aws_lb_target_group" "app" {
  name                 = "${var.project_name}-tg"
  vpc_id               = aws_vpc.app.id
  port                 = var.container_port
  protocol             = "HTTP"
  target_type          = "ip"
  deregistration_delay = 30
  health_check {
    path                = var.health_path
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = tostring(var.expected_status_code)
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}
resource "aws_lb_listener" "https" {
  count             = var.exposure_mode == "https_existing_certificate" ? 1 : 0
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
resource "aws_lb_listener" "redirect" {
  count             = var.exposure_mode == "https_existing_certificate" ? 1 : 0
  load_balancer_arn = aws_lb.app.arn
  port              = 80
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
resource "aws_lb_listener" "http_demo" {
  count             = var.exposure_mode == "http_demo" ? 1 : 0
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
resource "aws_ecs_service" "app" {
  name                               = var.project_name
  cluster                            = aws_ecs_cluster.app.id
  task_definition                    = aws_ecs_task_definition.app.arn
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  desired_count                      = local.size.desired_count
  enable_execute_command             = false
  health_check_grace_period_seconds  = 90
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = var.container_port
  }
  depends_on = [aws_lb_listener.https, aws_lb_listener.redirect, aws_lb_listener.http_demo, aws_iam_role_policy.execution, aws_route.internet]
}
