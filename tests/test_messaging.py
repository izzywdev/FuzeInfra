"""
Tests for messaging services: Kafka and RabbitMQ
"""
import pytest
import time
import json
import pika
from kafka import KafkaConsumer
from kafka.errors import KafkaError


class TestKafka:
    """Test Apache Kafka messaging service."""
    
    def test_kafka_producer_connection(self, kafka_producer, wait_for_services):
        """Test Kafka producer connection."""
        # Test that producer is connected
        assert kafka_producer.bootstrap_connected() is True

    def test_kafka_message_production(self, kafka_producer, wait_for_services):
        """Test Kafka message production and consumption."""
        topic_name = "test_topic"
        test_message = "Hello, Kafka!"
        
        # Send message
        future = kafka_producer.send(topic_name, test_message)
        record_metadata = future.get(timeout=10)
        
        assert record_metadata.topic == topic_name
        assert record_metadata.partition >= 0
        assert record_metadata.offset >= 0
        
        # Consume message to verify it was sent
        consumer = KafkaConsumer(
            topic_name,
            bootstrap_servers=["localhost:29092"],
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            group_id="test_group",
            value_deserializer=lambda x: x.decode("utf-8"),
            consumer_timeout_ms=5000
        )
        
        messages = []
        for message in consumer:
            messages.append(message.value)
            break  # Only consume one message
        
        consumer.close()
        assert test_message in messages

    def test_kafka_json_messages(self, wait_for_services):
        """Test Kafka with JSON messages."""
        from kafka import KafkaProducer, KafkaConsumer
        
        topic_name = "test_json_topic"
        test_data = {"id": 1, "name": "test", "timestamp": "2024-01-01T00:00:00"}
        
        # Producer with JSON serializer
        producer = KafkaProducer(
            bootstrap_servers=["localhost:29092"],
            value_serializer=lambda x: json.dumps(x).encode("utf-8")
        )
        
        # Send JSON message
        future = producer.send(topic_name, test_data)
        record_metadata = future.get(timeout=10)
        producer.close()
        
        assert record_metadata.topic == topic_name
        
        # Consumer with JSON deserializer
        consumer = KafkaConsumer(
            topic_name,
            bootstrap_servers=["localhost:29092"],
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            group_id="test_json_group",
            value_deserializer=lambda x: json.loads(x.decode("utf-8")),
            consumer_timeout_ms=5000
        )
        
        messages = []
        for message in consumer:
            messages.append(message.value)
            break
        
        consumer.close()
        assert len(messages) > 0
        assert messages[0]["name"] == "test"


class TestRabbitMQ:
    """Test RabbitMQ messaging service."""
    
    def test_rabbitmq_connection(self, rabbitmq_connection, wait_for_services):
        """Test RabbitMQ connection."""
        assert rabbitmq_connection.is_open is True

    def test_rabbitmq_queue_operations(self, rabbitmq_connection, wait_for_services):
        """Test RabbitMQ queue operations."""
        channel = rabbitmq_connection.channel()
        queue_name = "test_queue"
        
        # Declare queue
        channel.queue_declare(queue=queue_name, durable=False)
        
        # Publish message
        test_message = "Hello, RabbitMQ!"
        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=test_message
        )
        
        # Consume message
        method_frame, header_frame, body = channel.basic_get(queue=queue_name)
        
        assert body.decode("utf-8") == test_message
        
        # Acknowledge message
        if method_frame:
            channel.basic_ack(method_frame.delivery_tag)
        
        # Cleanup
        channel.queue_delete(queue=queue_name)
        channel.close()

    def test_rabbitmq_exchange_routing(self, rabbitmq_connection, wait_for_services):
        """Test RabbitMQ exchange and routing."""
        channel = rabbitmq_connection.channel()
        exchange_name = "test_exchange"
        queue_name = "test_routing_queue"
        routing_key = "test.routing.key"
        
        # Declare exchange and queue
        channel.exchange_declare(exchange=exchange_name, exchange_type="direct")
        channel.queue_declare(queue=queue_name, durable=False)
        channel.queue_bind(exchange=exchange_name, queue=queue_name, routing_key=routing_key)
        
        # Publish message to exchange
        test_message = "Routed message"
        channel.basic_publish(
            exchange=exchange_name,
            routing_key=routing_key,
            body=test_message
        )
        
        # Consume message
        method_frame, header_frame, body = channel.basic_get(queue=queue_name)
        
        assert body.decode("utf-8") == test_message
        
        # Acknowledge and cleanup
        if method_frame:
            channel.basic_ack(method_frame.delivery_tag)
        
        channel.queue_delete(queue=queue_name)
        channel.exchange_delete(exchange=exchange_name)
        channel.close()

    def test_rabbitmq_json_messages(self, rabbitmq_connection, wait_for_services):
        """Test RabbitMQ with JSON messages."""
        channel = rabbitmq_connection.channel()
        queue_name = "test_json_queue"
        
        # Declare queue
        channel.queue_declare(queue=queue_name, durable=False)
        
        # Publish JSON message
        test_data = {"id": 1, "name": "test", "timestamp": "2024-01-01T00:00:00"}
        channel.basic_publish(
            exchange="",
            routing_key=queue_name,
            body=json.dumps(test_data),
            properties=pika.BasicProperties(content_type="application/json")
        )
        
        # Consume message
        method_frame, header_frame, body = channel.basic_get(queue=queue_name)
        
        received_data = json.loads(body.decode("utf-8"))
        assert received_data["name"] == "test"
        assert received_data["id"] == 1
        
        # Acknowledge and cleanup
        if method_frame:
            channel.basic_ack(method_frame.delivery_tag)
        
        channel.queue_delete(queue=queue_name)
        channel.close() 