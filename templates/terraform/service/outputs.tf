output "application_url" {
  description = "HTTPS requires manual certificate-matching DNS. HTTP demo is restricted and unencrypted."
  value       = var.exposure_mode == "https_existing_certificate" ? try("https://${var.domain_name}", null) : "http://${aws_lb.app.dns_name}"
}
output "alb_dns_name" {
  value = aws_lb.app.dns_name
}
output "cluster_name" {
  value = aws_ecs_cluster.app.name
}
output "service_name" {
  value = aws_ecs_service.app.name
}
output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}
output "execution_role_arn" {
  value = local.execution_role_arn
}
output "image_uri" {
  value = var.image_uri
}
