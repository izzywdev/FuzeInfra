output "cluster_name" {
  description = "EKS cluster name."
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = module.eks.cluster_endpoint
}

output "cluster_region" {
  description = "AWS region of the cluster."
  value       = var.aws_region
}

output "configure_kubectl" {
  description = "Command to update your local kubeconfig for this cluster."
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

output "vpc_id" {
  description = "VPC ID hosting the cluster."
  value       = module.vpc.vpc_id
}
