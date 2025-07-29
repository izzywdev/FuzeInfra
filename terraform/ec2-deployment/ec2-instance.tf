# EC2 Instance Creation Resources
# This file handles creating new EC2 instances when they don't exist

# Data source to check if instance exists (may fail)
data "aws_instance" "fuzeinfra_instance_check" {
  count = var.create_instance ? 0 : 1
  
  filter {
    name   = "tag:Name"
    values = [var.instance_name]
  }
  
  filter {
    name   = "instance-state-name"
    values = ["running", "stopped"]
  }
}

# Get default VPC if creating new instance
data "aws_vpc" "default" {
  count   = var.create_instance ? 1 : 0
  default = true
}

# Get default subnet if creating new instance
data "aws_subnet" "default" {
  count             = var.create_instance ? 1 : 0
  vpc_id            = data.aws_vpc.default[0].id
  availability_zone = var.availability_zone
  default_for_az    = true
}

# Get latest Ubuntu AMI
data "aws_ami" "ubuntu" {
  count       = var.create_instance ? 1 : 0
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# Create new EC2 instance if requested
resource "aws_instance" "fuzeinfra_new" {
  count                  = var.create_instance ? 1 : 0
  ami                    = data.aws_ami.ubuntu[0].id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.fuzeinfra[0].key_name
  vpc_security_group_ids = [aws_security_group.fuzeinfra.id]
  subnet_id              = data.aws_subnet.default[0].id
  iam_instance_profile   = aws_iam_instance_profile.fuzeinfra.name

  # User data script for initial setup
  user_data = base64encode(templatefile("${path.module}/user-data.sh", {
    instance_name = var.instance_name
    environment   = var.environment
  }))

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
  count    = var.create_instance ? 1 : 0
  instance = aws_instance.fuzeinfra_new[0].id
  domain   = "vpc"

  tags = {
    Name = "${var.instance_name}-eip"
  }

  depends_on = [aws_instance.fuzeinfra_new]
}

# Output the correct instance information based on creation mode
locals {
  # Use existing instance if not creating, otherwise use new instance
  instance_id = var.create_instance ? aws_instance.fuzeinfra_new[0].id : data.aws_instance.fuzeinfra_instance_check[0].id
  instance_public_ip = var.create_instance ? aws_eip.fuzeinfra_new[0].public_ip : data.aws_instance.fuzeinfra_instance_check[0].public_ip
  instance_private_ip = var.create_instance ? aws_instance.fuzeinfra_new[0].private_ip : data.aws_instance.fuzeinfra_instance_check[0].private_ip
  vpc_id = var.create_instance ? data.aws_vpc.default[0].id : data.aws_instance.fuzeinfra_instance_check[0].vpc_id
  subnet_id = var.create_instance ? data.aws_subnet.default[0].id : data.aws_instance.fuzeinfra_instance_check[0].subnet_id
  availability_zone = var.create_instance ? aws_instance.fuzeinfra_new[0].availability_zone : data.aws_instance.fuzeinfra_instance_check[0].availability_zone
}

# CloudWatch alarms for new instance
resource "aws_cloudwatch_metric_alarm" "high_cpu" {
  count               = var.create_instance && var.enable_monitoring ? 1 : 0
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
    InstanceId = aws_instance.fuzeinfra_new[0].id
  }

  tags = {
    Name = "${var.instance_name}-cpu-alarm"
  }
}

resource "aws_cloudwatch_metric_alarm" "instance_status_check" {
  count               = var.create_instance && var.enable_monitoring ? 1 : 0
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
    InstanceId = aws_instance.fuzeinfra_new[0].id
  }

  tags = {
    Name = "${var.instance_name}-status-alarm"
  }
}