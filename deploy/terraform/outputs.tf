output "release_name" {
  description = "The Helm release name."
  value       = helm_release.bass.name
}

output "namespace" {
  description = "Namespace Bass was deployed into."
  value       = kubernetes_namespace.bass.metadata[0].name
}
