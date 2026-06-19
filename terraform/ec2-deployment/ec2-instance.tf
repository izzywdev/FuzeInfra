# EC2 Instance Creation Resources
# This file handles creating new EC2 instances when they don't exist

# Data source to check if instance exists by name/tag
data "aws_instances" "fuzeinfra_instance_check" {
  filter {
    name   = "tag:Name"
    values = [var.instance_name]
  }
  
  filter {
    name   = "instance-state-name"
    values = ["running", "stopped", "pending"]
  }
}

# Local to determine if we should create instance
locals {
  # Temporarily force instance creation since data source is timing out
  instance_exists = false  # Override to force creation
  should_create_instance = var.create_instance && !local.instance_exists
}

# Hardcoded values to bypass data source timeouts
locals {
  # Known default VPC and subnet for us-east-1
  default_vpc_id = "vpc-03087eb29ab79b8bb"  # Default VPC in us-east-1
  default_subnet_id = "subnet-023c7dbcdfffa5310"  # Subnet in us-east-1b  
  ubuntu_ami_id = "ami-0e2c8caa4b6378d8c"  # Ubuntu 22.04 LTS for us-east-1
}

# Create new EC2 instance if it doesn't exist
resource "aws_instance" "fuzeinfra_new" {
  count                  = local.should_create_instance ? 1 : 0
  ami                    = local.ubuntu_ami_id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.fuzeinfra[0].key_name
  vpc_security_group_ids = [aws_security_group.fuzeinfra.id]
  subnet_id              = local.default_subnet_id
  iam_instance_profile   = aws_iam_instance_profile.fuzeinfra.name

  # User data script for initial setup (using bootstrap to avoid 16KB limit)
  user_data = base64encode(file("${path.module}/bootstrap.sh"))

  # Enhanced monitoring
  monitoring = true

  # EBS optimized for better performance
  ebs_optimized = true

  # Root volume configuration
  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size
    encrypted             = true
    delete_on_termination = true
    
    tags = {
      Name = "${var.instance_name}-root"
    }
  }

  # Additional EBS volume for Docker data
  ebs_block_device {
    device_name           = "/dev/sdf"
    volume_type           = "gp3"
    volume_size           = var.docker_volume_size
    encrypted             = true
    delete_on_termination = false
    
    tags = {
      Name = "${var.instance_name}-docker"
    }
  }

  tags = {
    Name        = var.instance_name
    Environment = var.environment
    Project     = "FuzeInfra"
    CreatedBy   = "Terraform"
    Purpose     = "Infrastructure Platform"
  }

  lifecycle {
    create_before_destroy = false
    ignore_changes        = [ami] # Don't replace instance for AMI updates
  }
}

# Create Elastic IP for new instance
resource "aws_eip" "fuzeinfra_new" {
  count    = local.should_create_instance ? 1 : 0
  instance = aws_instance.fuzeinfra_new[0].id
  domain   = "vpc"

  tags = {
    Name = "${var.instance_name}-eip"
  }

  depends_on = [aws_instance.fuzeinfra_new]
}

# Additional locals for instance management
locals {
  # Determine instance details based on whether we created new or using existing
  instance_id = local.should_create_instance ? aws_instance.fuzeinfra_new[0].id : (
    local.instance_exists ? data.aws_instances.fuzeinfra_instance_check.ids[0] : ""
  )
  
  instance_public_ip = local.should_create_instance ? aws_eip.fuzeinfra_new[0].public_ip : (
    local.instance_exists ? data.aws_instances.fuzeinfra_instance_check.public_ips[0] : ""
  )
  
  instance_private_ip = local.should_create_instance ? aws_instance.fuzeinfra_new[0].private_ip : (
    local.instance_exists ? data.aws_instances.fuzeinfra_instance_check.private_ips[0] : ""
  )
  
  vpc_id = local.should_create_instance ? local.default_vpc_id : ""
  vpc_cidr = local.should_create_instance ? "172.31.0.0/16" : "10.0.0.0/16"
  subnet_id = local.should_create_instance ? local.default_subnet_id : ""
  availability_zone = local.should_create_instance ? "us-east-1b" : ""
}

# CloudWatch alarms for instance (new or existing)
resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  count               = (local.should_create_instance || local.instance_exists) && var.enable_monitoring ? 1 : 0
  alarm_name          = "${var.instance_name}-cpu-utilization"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = "300"
  statistic           = "Average"
  threshold           = "80"
  alarm_description   = "This metric monitors ec2 cpu utilization"
  alarm_actions       = var.sns_topic_arn != "" ? [var.sns_topic_arn] : []

  dimensions = {
    InstanceId = local.instance_id
  }

  tags = {
    Name = "${var.instance_name}-cpu-alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "instance_status_check" {
  count               = (local.should_create_instance || local.instance_exists) && var.enable_monitoring ? 1 : 0
  alarm_name          = "${var.instance_name}-status-check"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = "2"
  metric_name         = "StatusCheckFailed"
  namespace           = "AWS/EC2"
  period              = "300"
  statistic           = "Maximum"
  threshold           = "0"
  alarm_description   = "This metric monitors ec2 status check"
  alarm_actions       = var.sns_topic_arn != "" ? [var.sns_topic_arn] : []

  dimensions = {
    InstanceId = local.instance_id
  }

  tags = {
    Name = "${var.instance_name}-status-alarm"
  }
}