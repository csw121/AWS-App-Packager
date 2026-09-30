variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.aws_region))
    error_message = "Supply a reviewed AWS commercial region."
  }
}

variable "repository_name" {
  type        = string
  description = "A NEW dedicated ECR repository; no import or overwrite is performed."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,63}$", var.repository_name))
    error_message = "Use 2-64 lowercase letters, digits and hyphens, starting with a letter."
  }
}
