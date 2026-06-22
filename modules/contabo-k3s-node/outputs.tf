output "nodes" {
  description = "Map of request name → provisioned node details (instance id + public IPv4)."
  value = {
    for name, n in contabo_instance.node : name => {
      instance_id = n.id
      ipv4        = try(n.ip_config[0].v4[0].ip, null)
    }
  }
}

output "node_ids" {
  description = "List of Contabo instance IDs created."
  value       = [for n in contabo_instance.node : n.id]
}

output "private_network_id" {
  description = "ID of the attached Contabo private network, or null when disabled."
  value       = try(contabo_private_network.this[0].id, null)
}
