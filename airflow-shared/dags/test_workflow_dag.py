"""
Test DAG: Simple Workflow for Automated Testing

This DAG is specifically designed for automated testing of Airflow and Celery functionality.
It includes simple tasks that test basic operations without external dependencies.

Author: FuzeInfra Platform
Tags: test, automation, celery
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
import time
import random


# Default arguments for the DAG
default_args = {
    'owner': 'fuzeinfra-test',
    'depends_on_past': False,
    'start_date': datetime(2024, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=1),
}

# Create the DAG
dag = DAG(
    'test_workflow_simple',
    default_args=default_args,
    description='Simple test workflow for automated testing',
    schedule_interval=None,  # Manual trigger only
    catchup=False,
    tags=['test', 'automation', 'celery'],
)


def simple_python_task():
    """Simple Python task to test basic execution."""
    import os
    import platform
    
    print("=== Simple Python Task Execution ===")
    print(f"✅ Python version: {platform.python_version()}")
    print(f"✅ Platform: {platform.system()} {platform.release()}")
    print(f"✅ Current working directory: {os.getcwd()}")
    print(f"✅ Task executed at: {datetime.now()}")
    
    # Test basic operations
    test_data = {"message": "Hello from Airflow!", "timestamp": str(datetime.now())}
    print(f"✅ Test data created: {test_data}")
    
    return test_data


def cpu_intensive_task():
    """CPU-intensive task to test worker processing."""
    print("=== CPU Intensive Task Started ===")
    
    # Simulate CPU-intensive work
    start_time = time.time()
    result = 0
    
    for i in range(1000000):
        result += i * random.random()
    
    end_time = time.time()
    duration = end_time - start_time
    
    print(f"✅ CPU task completed in {duration:.2f} seconds")
    print(f"✅ Result: {result:.2f}")
    
    return {"duration": duration, "result": result}


def memory_task():
    """Memory allocation task to test worker memory handling."""
    print("=== Memory Task Started ===")
    
    # Create some data structures
    data_list = []
    for i in range(10000):
        data_list.append({
            "id": i,
            "value": f"test_value_{i}",
            "timestamp": datetime.now(),
            "random": random.random()
        })
    
    print(f"✅ Created list with {len(data_list)} items")
    
    # Process the data
    processed_count = 0
    for item in data_list:
        if item["random"] > 0.5:
            processed_count += 1
    
    print(f"✅ Processed {processed_count} items")
    
    return {"total_items": len(data_list), "processed_items": processed_count}


def task_with_dependencies():
    """Task that depends on other tasks completing."""
    print("=== Dependency Task Started ===")
    print("✅ All prerequisite tasks have completed successfully")
    print("✅ This task demonstrates proper dependency handling")
    
    # Simulate some work
    time.sleep(2)
    
    final_result = {
        "status": "completed",
        "message": "All tasks in workflow completed successfully",
        "completion_time": str(datetime.now())
    }
    
    print(f"✅ Final result: {final_result}")
    return final_result


# Define tasks
task_1 = PythonOperator(
    task_id='simple_python_task',
    python_callable=simple_python_task,
    dag=dag,
)

task_2 = PythonOperator(
    task_id='cpu_intensive_task',
    python_callable=cpu_intensive_task,
    dag=dag,
)

task_3 = PythonOperator(
    task_id='memory_task',
    python_callable=memory_task,
    dag=dag,
)

# Simple bash task
bash_task = BashOperator(
    task_id='simple_bash_task',
    bash_command='''
    echo "=== Bash Task Execution ==="
    echo "✅ Hostname: $(hostname)"
    echo "✅ Date: $(date)"
    echo "✅ User: $(whoami)"
    echo "✅ Working directory: $(pwd)"
    echo "✅ Bash task completed successfully"
    ''',
    dag=dag,
)

# Final dependency task
final_task = PythonOperator(
    task_id='final_dependency_task',
    python_callable=task_with_dependencies,
    dag=dag,
)

# Set task dependencies
# Tasks 1, 2, 3 can run in parallel
# Bash task runs after task 1
# Final task runs after all others complete
task_1 >> bash_task
[task_1, task_2, task_3, bash_task] >> final_task 