# All resources use this sole AWS provider. No aliases, data sources or real providers.
mock_provider "aws" {
  override_during = plan
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock-execution", id = "mock-execution" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-northeast-2:123456789012:log-group:/aws-app-packager/mock" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:loadbalancer/app/mock/1234567890123456", dns_name = "mock-alb.example.invalid" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:123456789012:targetgroup/mock/1234567890123456" }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { id = "arn:aws:ecs:ap-northeast-2:123456789012:cluster/mock" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:ap-northeast-2:123456789012:task-definition/mock:1" }
  }
  mock_resource "aws_security_group" {
    defaults = { id = "sg-0123456789abcdef0" }
  }
  mock_resource "aws_vpc" {
    defaults = { id = "vpc-0123456789abcdef0" }
  }
  mock_resource "aws_subnet" {
    defaults = { id = "subnet-0123456789abcdef0" }
  }
}

variables {
  project_name       = "packager-mock"
  availability_zones = ["ap-northeast-2a", "ap-northeast-2c"]
  container_port     = 8080
  health_path        = "/health"
  container_user     = "1000:1000"
  image_uri          = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/packager-test@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  allowed_cidrs      = ["203.0.113.10/32"]
  exposure_mode      = "http_demo"
  environment        = { PORT = "8080" }
}

run "small_http_new_role" {
  command = plan
  variables { preset = "small" }
  assert {
    condition     = aws_ecs_task_definition.app.cpu == "512" && aws_ecs_task_definition.app.memory == "1024" && aws_ecs_service.app.desired_count == 1
    error_message = "Small preset values changed."
  }
  assert {
    condition     = aws_ecs_task_definition.app.network_mode == "awsvpc" && toset(aws_ecs_task_definition.app.requires_compatibilities) == toset(["FARGATE"]) && aws_ecs_task_definition.app.runtime_platform[0].cpu_architecture == "X86_64" && aws_ecs_task_definition.app.runtime_platform[0].operating_system_family == "LINUX"
    error_message = "Platform must remain Linux/X86_64 Fargate awsvpc."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.app.container_definitions)[0].image == var.image_uri && jsondecode(aws_ecs_task_definition.app.container_definitions)[0].portMappings[0].containerPort == 8080 && jsondecode(aws_ecs_task_definition.app.container_definitions)[0].user == "1000:1000"
    error_message = "Tested image, port and user must be preserved."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.app.container_definitions)[0].readonlyRootFilesystem && jsondecode(aws_ecs_task_definition.app.container_definitions)[0].mountPoints[0].containerPath == "/tmp" && !contains(keys(jsondecode(aws_ecs_task_definition.app.container_definitions)[0]), "command") && !contains(keys(jsondecode(aws_ecs_task_definition.app.container_definitions)[0]), "entryPoint")
    error_message = "Readonly root, task ephemeral mount and image command preservation are required."
  }
  assert {
    condition     = jsondecode(aws_ecs_task_definition.app.container_definitions)[0].environment[0].value == "8080" && aws_lb_target_group.app.port == 8080 && aws_lb_target_group.app.target_type == "ip" && aws_lb_target_group.app.health_check[0].path == "/health" && aws_lb_target_group.app.health_check[0].matcher == "200"
    error_message = "The ALB health check and environment must agree with the tested conditions."
  }
  assert {
    condition     = length(aws_lb_listener.http_demo) == 1 && length(aws_lb_listener.https) == 0 && length(aws_lb_listener.redirect) == 0 && length(aws_vpc_security_group_ingress_rule.alb_clients) == 1 && aws_vpc_security_group_ingress_rule.alb_clients["80:203.0.113.10/32"].cidr_ipv4 == "203.0.113.10/32"
    error_message = "HTTP demo must remain explicitly selected and CIDR-restricted."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.task_from_alb.referenced_security_group_id == aws_security_group.alb.id && aws_vpc_security_group_ingress_rule.task_from_alb.from_port == var.container_port && aws_vpc_security_group_ingress_rule.task_from_alb.cidr_ipv4 == null && aws_vpc_security_group_egress_rule.alb_to_task.referenced_security_group_id == aws_security_group.task.id
    error_message = "Task ingress must originate only from ALB on the tested port."
  }
  assert {
    condition     = length(aws_iam_role.execution) == 1 && length(aws_iam_role_policy.execution) == 1 && jsondecode(aws_iam_role_policy.execution[0].policy).Statement[1].Resource == "arn:aws:ecr:ap-northeast-2:123456789012:repository/packager-test" && jsondecode(aws_iam_role_policy.execution[0].policy).Statement[2].Resource == "${aws_cloudwatch_log_group.app.arn}:*"
    error_message = "Execution permission must be limited to the supplied repository and log group."
  }
  assert {
    condition     = length(aws_subnet.public) == 2 && aws_ecs_service.app.network_configuration[0].assign_public_ip && aws_cloudwatch_log_group.app.retention_in_days == 7
    error_message = "The public-subnet/public-IP/log-retention design changed."
  }
}

run "medium_https_existing_role" {
  command = plan
  variables {
    preset                      = "medium"
    exposure_mode               = "https_existing_certificate"
    certificate_arn             = "arn:aws:acm:ap-northeast-2:123456789012:certificate/12345678-1234-1234-1234-123456789012"
    domain_name                 = "app.example.com"
    create_execution_role       = false
    existing_execution_role_arn = "arn:aws:iam::123456789012:role/ReviewedExecutionRole"
  }
  assert {
    condition     = aws_ecs_task_definition.app.cpu == "1024" && aws_ecs_task_definition.app.memory == "2048" && aws_ecs_service.app.desired_count == 1
    error_message = "Medium preset values changed."
  }
  assert {
    condition     = length(aws_iam_role.execution) == 0 && length(aws_iam_role_policy.execution) == 0 && aws_ecs_task_definition.app.execution_role_arn == var.existing_execution_role_arn
    error_message = "Existing role must be used without being created, modified or deleted."
  }
  assert {
    condition     = length(aws_lb_listener.http_demo) == 0 && aws_lb_listener.https[0].port == 443 && aws_lb_listener.https[0].certificate_arn == var.certificate_arn && aws_lb_listener.https[0].ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06" && aws_lb_listener.redirect[0].default_action[0].redirect[0].protocol == "HTTPS" && output.application_url == "https://app.example.com"
    error_message = "HTTPS must use the real domain/certificate, TLS policy and redirect."
  }
}

run "large_same_image" {
  command = plan
  variables { preset = "large" }
  assert {
    condition     = aws_ecs_task_definition.app.cpu == "2048" && aws_ecs_task_definition.app.memory == "4096" && aws_ecs_service.app.desired_count == 1 && jsondecode(aws_ecs_task_definition.app.container_definitions)[0].image == var.image_uri
    error_message = "Large preset must keep the same image and desired count."
  }
}

run "reject_unrestricted_http" {
  command = plan
  variables { allowed_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.allowed_cidrs]
}
run "reject_missing_certificate" {
  command = plan
  variables {
    exposure_mode = "https_existing_certificate"
    domain_name   = "app.example.com"
  }
  expect_failures = [var.certificate_arn]
}
run "reject_missing_existing_role" {
  command = plan
  variables { create_execution_role = false }
  expect_failures = [var.existing_execution_role_arn]
}
run "reject_http_tls_fields" {
  command = plan
  variables {
    certificate_arn = "arn:aws:acm:ap-northeast-2:123456789012:certificate/12345678-1234-1234-1234-123456789012"
    domain_name     = "app.example.com"
  }
  expect_failures = [var.certificate_arn, var.domain_name]
}
run "reject_zero_uid" {
  command = plan
  variables { container_user = "00" }
  expect_failures = [var.container_user]
}
run "reject_non_digest_image" {
  command = plan
  variables { image_uri = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
  expect_failures = [var.image_uri]
}
run "reject_missing_domain" {
  command = plan
  variables {
    exposure_mode   = "https_existing_certificate"
    certificate_arn = "arn:aws:acm:ap-northeast-2:123456789012:certificate/12345678-1234-1234-1234-123456789012"
  }
  expect_failures = [var.domain_name]
}
