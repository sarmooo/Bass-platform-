# Deploys the Bass Helm chart as infrastructure-as-code — the IaC mirror of a
# `helm upgrade`. `terraform validate` checks this in CI; a real `apply` needs a
# reachable cluster (var.kubeconfig_path) and the secrets below.

resource "kubernetes_namespace" "bass" {
  metadata {
    name = var.namespace
  }
}

resource "helm_release" "bass" {
  name      = "bass"
  namespace = kubernetes_namespace.bass.metadata[0].name
  chart     = "${path.module}/../helm/bass"

  values = [yamlencode({
    image = {
      tag = var.image_tag
    }
    replicaCount = var.replica_count
    autoscaling = {
      enabled = var.autoscaling_enabled
    }
    secret = {
      create      = true
      jwtSecret   = var.jwt_secret
      databaseUrl = var.database_url
    }
    ingress = {
      enabled = var.ingress_host != ""
      hosts = [{
        host  = var.ingress_host
        paths = [{ path = "/", pathType = "Prefix" }]
      }]
    }
  })]
}
