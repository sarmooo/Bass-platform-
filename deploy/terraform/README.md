# Terraform (IaC deploy)

Deploys the Bass Helm chart as infrastructure-as-code — the Terraform equivalent
of a `helm upgrade`, so the deployment is versioned and reviewable alongside the
rest of the repo. Validated in CI (`.github/workflows/terraform.yml`:
`terraform validate`); a real `apply` targets your cluster.

```bash
cd deploy/terraform
terraform init
terraform apply \
  -var image_tag=v0.1.0 \
  -var database_url='postgresql://user:pass@host:5432/bass' \
  -var jwt_secret="$(openssl rand -hex 32)" \
  -var ingress_host=bass.example.com
```

Providers: `hashicorp/kubernetes` + `hashicorp/helm`, both pointed at
`var.kubeconfig_path`. Secrets (`jwt_secret`, `database_url`) are marked
sensitive — pass them via a secrets manager or `TF_VAR_*`, never commit them.
