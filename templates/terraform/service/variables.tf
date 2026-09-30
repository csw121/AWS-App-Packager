variable "project_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,23}$", var.project_name))
    error_message = "Use a safe name of at most 24 characters."
  }
}
variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]$", var.aws_region))
    error_message = "Supply a reviewed AWS commercial region."
  }
}
variable "availability_zones" {
  type        = list(string)
  description = "Exactly two distinct AZ names, checked by the user for their account and region."
  validation {
    condition = length(var.availability_zones) == 2 && length(toset(var.availability_zones)) == 2 && alltrue([
      for az in var.availability_zones : can(regex("^${var.aws_region}[a-z]$", az))
    ])
    error_message = "Supply two distinct AZ names in aws_region."
  }
}
variable "vpc_cidr" {
  type    = string
  default = "10.72.0.0/16"
  validation {
    condition     = can(cidrnetmask(var.vpc_cidr)) && can(regex("/16$", var.vpc_cidr))
    error_message = "Supply an IPv4 /16 VPC CIDR after checking address overlap."
  }
}
variable "preset" {
  type    = string
  default = "medium"
  validation {
    condition     = contains(["small", "medium", "large"], var.preset)
    error_message = "Choose small, medium, or large; a changed preset requires another local test."
  }
}
variable "container_port" {
  type = number
  validation {
    condition     = var.container_port >= 1 && var.container_port <= 65535 && floor(var.container_port) == var.container_port
    error_message = "Supply the tested container port (1-65535)."
  }
}
variable "health_path" {
  type = string
  validation {
    condition     = can(regex("^/[A-Za-z0-9/_~.\\-]*$", var.health_path)) && !startswith(var.health_path, "//") && !contains(split("/", var.health_path), "..") && length(var.health_path) <= 200
    error_message = "Supply the tested local relative health path, without a host, query or traversal."
  }
}
variable "expected_status_code" {
  type    = number
  default = 200
  validation {
    condition     = var.expected_status_code == 200
    error_message = "This profile supports the tested HTTP 200 status only."
  }
}
variable "environment" {
  type    = map(string)
  default = {}
  validation {
    condition = length(var.environment) <= 6 && alltrue([
      for key, value in var.environment : contains(["PORT", "SERVER_PORT", "TZ", "LANG", "BPL_JVM_THREAD_COUNT", "BPL_JVM_HEAD_ROOM"], key) && can(regex("^[A-Za-z0-9_./+\\-]{1,80}$", value))
    ])
    error_message = "Only the tested non-sensitive allowlisted environment settings are supported."
  }
}
variable "image_uri" {
  type        = string
  description = "ECR repository URI@sha256:MANIFEST_DIGEST after a manual push; never a local image ID."
  validation {
    condition     = can(regex("^[0-9]{12}\\.dkr\\.ecr\\.${var.aws_region}\\.amazonaws\\.com/[a-z0-9]+([._/-][a-z0-9]+)*@sha256:[a-f0-9]{64}$", var.image_uri))
    error_message = "Supply a manually verified ECR manifest digest URI in aws_region."
  }
}
variable "container_user" {
  type        = string
  description = "The non-root image USER observed during the local test."
  validation {
    condition     = can(regex("^[1-9][0-9]*(:[0-9]+)?$", var.container_user))
    error_message = "Supply the tested explicit numeric nonzero image UID, optionally with a numeric GID."
  }
}
variable "exposure_mode" {
  type    = string
  default = "https_existing_certificate"
  validation {
    condition     = contains(["https_existing_certificate", "http_demo"], var.exposure_mode)
    error_message = "Choose https_existing_certificate or the explicit restricted http_demo mode."
  }
}
variable "allowed_cidrs" {
  type        = list(string)
  description = "Reviewed IPv4 ingress ranges. Restricted CIDRs are mandatory; /0 is never accepted."
  validation {
    condition = length(var.allowed_cidrs) > 0 && length(var.allowed_cidrs) <= 20 && alltrue([
      for block in var.allowed_cidrs : can(cidrnetmask(block)) && !can(regex("/0$", block))
    ])
    error_message = "Supply 1-20 valid IPv4 CIDRs; unrestricted /0 ingress is not allowed."
  }
}
variable "certificate_arn" {
  type     = string
  default  = null
  nullable = true
  validation {
    condition     = var.exposure_mode == "http_demo" ? var.certificate_arn == null : can(regex("^arn:aws:acm:${var.aws_region}:[0-9]{12}:certificate/[a-f0-9-]+$", var.certificate_arn))
    error_message = "HTTPS requires an existing ACM certificate ARN in aws_region."
  }
}
variable "domain_name" {
  type     = string
  default  = null
  nullable = true
  validation {
    condition     = var.exposure_mode == "http_demo" ? var.domain_name == null : can(regex("^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\\.[A-Za-z]{2,63}$", var.domain_name))
    error_message = "HTTPS requires the real certificate-matching DNS name; configure DNS manually."
  }
}
variable "create_execution_role" {
  type    = bool
  default = true
}
variable "existing_execution_role_arn" {
  type     = string
  default  = null
  nullable = true
  validation {
    condition     = var.create_execution_role ? var.existing_execution_role_arn == null : can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9_+=,.@/-]+$", var.existing_execution_role_arn))
    error_message = "Create a new role without an existing ARN, or provide a reviewed existing role ARN."
  }
}
