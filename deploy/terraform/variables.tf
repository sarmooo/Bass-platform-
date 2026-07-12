variable "kubeconfig_path" {
  description = "Path to the kubeconfig used to reach the target cluster."
  type        = string
  default     = "~/.kube/config"
}

variable "namespace" {
  description = "Namespace to deploy Bass into."
  type        = string
  default     = "bass"
}

variable "image_tag" {
  description = "Bass container image tag to deploy."
  type        = string
}

variable "replica_count" {
  description = "API replica count (ignored when autoscaling is enabled)."
  type        = number
  default     = 2
}

variable "autoscaling_enabled" {
  description = "Enable the HorizontalPodAutoscaler."
  type        = bool
  default     = true
}

variable "ingress_host" {
  description = "Ingress host; empty string disables ingress."
  type        = string
  default     = ""
}

variable "jwt_secret" {
  description = "HS256 signing secret for the API."
  type        = string
  sensitive   = true
}

variable "database_url" {
  description = "PostgreSQL DSN (postgresql://user:pass@host:5432/bass)."
  type        = string
  sensitive   = true
}
