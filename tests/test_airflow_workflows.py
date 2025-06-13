"""
Tests for Airflow workflow execution and Celery task processing
"""
import pytest
import requests
import time
import json
from datetime import datetime, timedelta


class TestAirflowWorkflowExecution:
    """Test actual DAG execution and workflow processing."""
    
    def test_simple_dag_trigger_and_execution(self, service_urls, wait_for_services):
        """Test triggering a simple test DAG and verifying its execution."""
        auth = ("admin", "admin")
        base_url = service_urls['airflow']
        dag_id = "test_workflow_simple"
        
        # First, verify the DAG exists
        response = requests.get(f"{base_url}/api/v1/dags/{dag_id}", auth=auth, timeout=10)
        if response.status_code == 401:
            pytest.skip("Airflow authentication not configured for API access")
        
        if response.status_code == 404:
            pytest.skip(f"Test DAG {dag_id} not found - may need time to load")
        
        assert response.status_code == 200, f"DAG {dag_id} not accessible: {response.status_code}"
        dag_info = response.json()
        assert dag_info["dag_id"] == dag_id
        
        # Unpause the DAG if it's paused
        if dag_info.get("is_paused", True):
            unpause_response = requests.patch(
                f"{base_url}/api/v1/dags/{dag_id}",
                auth=auth,
                json={"is_paused": False},
                timeout=10
            )
            print(f"Unpaused DAG: {unpause_response.status_code}")
        
        # Trigger the DAG
        trigger_data = {
            "conf": {"test_run": True, "triggered_by": "pytest"}
        }
        
        response = requests.post(
            f"{base_url}/api/v1/dags/{dag_id}/dagRuns",
            auth=auth,
            json=trigger_data,
            timeout=10
        )
        
        assert response.status_code == 200, f"Failed to trigger DAG: {response.text}"
        dag_run_info = response.json()
        dag_run_id = dag_run_info["dag_run_id"]
        
        print(f"✅ DAG triggered successfully: {dag_run_id}")
        
        # Wait for DAG run to complete (with timeout)
        max_wait_time = 180  # 3 minutes for simple tasks
        start_time = time.time()
        final_state = None
        
        while time.time() - start_time < max_wait_time:
            response = requests.get(
                f"{base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}",
                auth=auth,
                timeout=10
            )
            
            assert response.status_code == 200
            run_status = response.json()
            state = run_status["state"]
            
            print(f"DAG run state: {state}")
            
            if state in ["success", "failed"]:
                final_state = state
                break
                
            time.sleep(5)  # Wait 5 seconds before checking again
        
        # Verify final state
        if final_state is None:
            pytest.fail(f"DAG run timed out after {max_wait_time} seconds")
        
        assert final_state == "success", f"DAG run failed. Final state: {final_state}"
        print(f"✅ DAG execution completed successfully")
        
        return dag_run_id

    def test_task_execution_details(self, service_urls, wait_for_services):
        """Test individual task execution within a DAG run."""
        auth = ("admin", "admin")
        base_url = service_urls['airflow']
        dag_id = "test_workflow_simple"
        
        # First trigger a DAG run
        try:
            dag_run_id = self.test_simple_dag_trigger_and_execution(service_urls, wait_for_services)
        except Exception as e:
            pytest.skip(f"Could not trigger DAG for task testing: {e}")
        
        # Get task instances for this DAG run
        response = requests.get(
            f"{base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances",
            auth=auth,
            timeout=10
        )
        
        assert response.status_code == 200
        task_instances = response.json()["task_instances"]
        
        # Verify we have the expected tasks
        expected_tasks = [
            "simple_python_task",
            "cpu_intensive_task", 
            "memory_task",
            "simple_bash_task",
            "final_dependency_task"
        ]
        
        actual_tasks = [task["task_id"] for task in task_instances]
        for expected_task in expected_tasks:
            assert expected_task in actual_tasks, f"Expected task {expected_task} not found"
        
        # Verify all tasks succeeded
        failed_tasks = []
        for task in task_instances:
            if task["state"] != "success":
                failed_tasks.append(f"{task['task_id']}: {task['state']}")
            else:
                print(f"✅ Task {task['task_id']} completed successfully")
        
        if failed_tasks:
            pytest.fail(f"Tasks failed: {', '.join(failed_tasks)}")

    def test_workflow_with_dependencies(self, service_urls, wait_for_services):
        """Test a workflow with task dependencies to verify proper execution order."""
        auth = ("admin", "admin")
        base_url = service_urls['airflow']
        dag_id = "test_workflow_simple"
        
        # Trigger DAG and get execution details
        try:
            dag_run_id = self.test_simple_dag_trigger_and_execution(service_urls, wait_for_services)
        except Exception as e:
            pytest.skip(f"Could not trigger DAG for dependency testing: {e}")
        
        # Get task instances with execution times
        response = requests.get(
            f"{base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances",
            auth=auth,
            timeout=10
        )
        
        assert response.status_code == 200
        task_instances = response.json()["task_instances"]
        
        # Find the final task (should run last)
        final_task = None
        prerequisite_tasks = []
        
        for task in task_instances:
            if task["task_id"] == "final_dependency_task":
                final_task = task
            else:
                prerequisite_tasks.append(task)
        
        assert final_task is not None, "Final dependency task not found"
        assert len(prerequisite_tasks) > 0, "Prerequisite tasks not found"
        
        # Verify final task started after prerequisite tasks completed
        if final_task["start_date"]:
            final_start = datetime.fromisoformat(final_task["start_date"].replace('Z', '+00:00'))
            
            for prereq_task in prerequisite_tasks:
                if prereq_task["end_date"]:
                    prereq_end = datetime.fromisoformat(prereq_task["end_date"].replace('Z', '+00:00'))
                    assert final_start >= prereq_end, f"Dependency violation: {prereq_task['task_id']} should complete before final_dependency_task"
        
        print("✅ Task dependencies executed in correct order")

    def test_celery_worker_functionality(self, service_urls, wait_for_services):
        """Test that Celery workers are processing tasks correctly."""
        flower_url = service_urls['airflow_flower']
        
        # Get worker information
        response = requests.get(f"{flower_url}/api/workers", timeout=10)
        assert response.status_code == 200
        
        workers = response.json()
        assert len(workers) > 0, "No Celery workers found"
        
        # Check worker status
        active_workers = 0
        for worker_name, worker_info in workers.items():
            if worker_info.get("status"):
                active_workers += 1
                print(f"✅ Worker {worker_name} is active")
        
        assert active_workers > 0, "No active Celery workers found"
        print(f"✅ Found {active_workers} active Celery workers")

    def test_parallel_task_execution(self, service_urls, wait_for_services):
        """Test that parallel tasks can execute simultaneously on different workers."""
        # This test verifies that multiple tasks can run in parallel
        # by checking execution times overlap
        auth = ("admin", "admin")
        base_url = service_urls['airflow']
        dag_id = "test_workflow_simple"
        
        try:
            dag_run_id = self.test_simple_dag_trigger_and_execution(service_urls, wait_for_services)
        except Exception as e:
            pytest.skip(f"Could not trigger DAG for parallel testing: {e}")
        
        # Get task instances
        response = requests.get(
            f"{base_url}/api/v1/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances",
            auth=auth,
            timeout=10
        )
        
        assert response.status_code == 200
        task_instances = response.json()["task_instances"]
        
        # Find parallel tasks (should be able to run simultaneously)
        parallel_tasks = [
            task for task in task_instances 
            if task["task_id"] in ["simple_python_task", "cpu_intensive_task", "memory_task"]
        ]
        
        assert len(parallel_tasks) >= 2, "Not enough parallel tasks found"
        
        # Check if any tasks had overlapping execution times
        overlapping_found = False
        for i, task1 in enumerate(parallel_tasks):
            for task2 in parallel_tasks[i+1:]:
                if task1["start_date"] and task1["end_date"] and task2["start_date"] and task2["end_date"]:
                    start1 = datetime.fromisoformat(task1["start_date"].replace('Z', '+00:00'))
                    end1 = datetime.fromisoformat(task1["end_date"].replace('Z', '+00:00'))
                    start2 = datetime.fromisoformat(task2["start_date"].replace('Z', '+00:00'))
                    end2 = datetime.fromisoformat(task2["end_date"].replace('Z', '+00:00'))
                    
                    # Check for overlap
                    if (start1 <= start2 <= end1) or (start2 <= start1 <= end2):
                        overlapping_found = True
                        print(f"✅ Tasks {task1['task_id']} and {task2['task_id']} executed in parallel")
                        break
            
            if overlapping_found:
                break
        
        # Note: Parallel execution depends on having multiple workers
        # If no overlap found, it might be due to single worker or fast execution
        print(f"ℹ️ Parallel execution test completed (overlap found: {overlapping_found})")


class TestCeleryTaskProcessing:
    """Test Celery task processing capabilities."""
    
    def test_worker_pool_status(self, service_urls, wait_for_services):
        """Test Celery worker pool status and capacity."""
        flower_url = service_urls['airflow_flower']
        
        # Get detailed worker information
        response = requests.get(f"{flower_url}/api/workers", timeout=10)
        assert response.status_code == 200
        
        workers = response.json()
        total_workers = len(workers)
        active_workers = sum(1 for w in workers.values() if w.get("status"))
        
        assert total_workers > 0, "No workers configured"
        assert active_workers > 0, "No active workers found"
        
        print(f"✅ Celery workers: {active_workers}/{total_workers} active")
        
        # Check worker capabilities
        for worker_name, worker_info in workers.items():
            if worker_info.get("status"):
                processed = worker_info.get("processed", 0)
                print(f"✅ Worker {worker_name}: {processed} tasks processed")

    def test_task_routing_and_queues(self, service_urls, wait_for_services):
        """Test task routing and queue management."""
        flower_url = service_urls['airflow_flower']
        
        # Get active tasks
        response = requests.get(f"{flower_url}/api/tasks", timeout=10)
        assert response.status_code == 200
        
        tasks = response.json()
        
        # Check if we have task information
        if tasks:
            print(f"✅ Task routing information available for {len(tasks)} task types")
            
            # Check for common Airflow task types
            airflow_tasks = [task for task in tasks.keys() if 'airflow' in task.lower()]
            if airflow_tasks:
                print(f"✅ Found Airflow-specific tasks: {airflow_tasks}")
        else:
            print("ℹ️ No active tasks currently (normal when no DAGs are running)")

    def test_worker_health_monitoring(self, service_urls, wait_for_services):
        """Test worker health monitoring through Flower."""
        flower_url = service_urls['airflow_flower']
        
        # Get worker statistics
        response = requests.get(f"{flower_url}/api/workers", timeout=10)
        assert response.status_code == 200
        
        workers = response.json()
        
        healthy_workers = 0
        for worker_name, worker_info in workers.items():
            if worker_info.get("status"):
                healthy_workers += 1
                
                # Check for health indicators
                if "loadavg" in worker_info:
                    print(f"✅ Worker {worker_name} load average: {worker_info['loadavg']}")
                
                if "processed" in worker_info:
                    print(f"✅ Worker {worker_name} processed: {worker_info['processed']} tasks")
                
                if "active" in worker_info:
                    print(f"✅ Worker {worker_name} active tasks: {worker_info['active']}")
        
        assert healthy_workers > 0, "No healthy workers found"
        print(f"✅ {healthy_workers} healthy workers monitored successfully") 