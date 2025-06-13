# Shared Airflow Configuration

This directory contains the shared Airflow setup for all projects in the FuzeInfra platform.

## Directory Structure

```
airflow-shared/
├── dags/           # DAG files from all projects
├── logs/           # Task execution logs
├── plugins/        # Shared Airflow plugins
└── README.md       # This file
```

## How Projects Use Shared Airflow

### 1. DAG Organization
Each project should organize their DAGs with clear naming conventions:

```
dags/
├── project_a_data_pipeline.py
├── project_a_ml_training.py
├── project_b_etl_daily.py
├── project_b_reporting.py
└── shared_maintenance_tasks.py
```

### 2. Adding DAGs from Your Project
Copy your DAG files to the `dags/` directory:

```bash
# From your project directory
cp your_project/dags/*.py /path/to/FuzeInfra/airflow-shared/dags/
```

Or use a symbolic link for development:

```bash
# Create symlink for development
ln -s /path/to/your_project/dags/your_dag.py /path/to/FuzeInfra/airflow-shared/dags/
```

### 3. DAG Best Practices

#### Naming Convention
- Use project prefix: `{project_name}_{dag_purpose}.py`
- Use descriptive names: `ecommerce_daily_sales_report.py`
- Avoid conflicts with other projects

#### DAG Configuration
```python
from airflow import DAG
from datetime import datetime, timedelta

default_args = {
    'owner': 'project_name',
    'depends_on_past': False,
    'start_date': datetime(2024, 1, 1),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

dag = DAG(
    'project_name_dag_name',
    default_args=default_args,
    description='Description of what this DAG does',
    schedule_interval=timedelta(days=1),
    catchup=False,
    tags=['project_name', 'category'],
)
```

#### Resource Management
- Use appropriate resource requirements
- Set reasonable timeouts
- Use task pools for resource-intensive operations
- Tag DAGs for easy filtering in UI

### 4. Accessing Shared Infrastructure

Your DAGs can access all shared infrastructure services:

```python
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.providers.redis.operators.redis import RedisOperator

# PostgreSQL connection
postgres_task = PostgresOperator(
    task_id='postgres_task',
    postgres_conn_id='postgres_default',  # Configure in Airflow UI
    sql='SELECT * FROM your_table;',
    dag=dag,
)

# Redis connection
redis_task = RedisOperator(
    task_id='redis_task',
    redis_conn_id='redis_default',  # Configure in Airflow UI
    command=['SET', 'key', 'value'],
    dag=dag,
)
```

### 5. Connection Configuration

Configure connections in Airflow UI (http://localhost:8082):

**PostgreSQL Connection:**
- Conn Id: `postgres_default`
- Conn Type: `Postgres`
- Host: `postgres`
- Schema: `your_database`
- Login: `postgres`
- Password: `[from .env file]`
- Port: `5432`

**Redis Connection:**
- Conn Id: `redis_default`
- Conn Type: `Redis`
- Host: `redis`
- Port: `6379`

**MongoDB Connection:**
- Conn Id: `mongodb_default`
- Conn Type: `MongoDB`
- Host: `mongodb`
- Port: `27017`
- Login: `admin`
- Password: `[from .env file]`

### 6. Monitoring and Logs

- **Airflow UI**: http://localhost:8082
- **Flower (Celery Monitor)**: http://localhost:5555
- **Task Logs**: Available in Airflow UI and `logs/` directory
- **Grafana Dashboards**: Monitor Airflow metrics in Grafana

### 7. Development Workflow

1. **Develop DAGs locally** in your project
2. **Test DAGs** using Airflow's testing utilities
3. **Copy/link DAGs** to shared directory
4. **Monitor execution** in Airflow UI
5. **Check logs** for debugging

### 8. Security Considerations

- **No sensitive data in DAG files** - use Airflow Variables/Connections
- **Use Airflow's encryption** for sensitive variables
- **Organize by project** to avoid conflicts
- **Use appropriate task isolation**

## Shared Resources

### Task Pools
Configure task pools in Airflow UI for resource management:
- `default_pool`: General tasks
- `database_pool`: Database-intensive tasks
- `ml_pool`: Machine learning tasks

### Variables
Store shared configuration in Airflow Variables:
- `shared_data_path`: Common data directory
- `notification_email`: Shared notification email
- `environment`: Current environment (dev/staging/prod)

## Troubleshooting

### Common Issues
1. **DAG not appearing**: Check file syntax and naming
2. **Import errors**: Ensure all dependencies are available
3. **Connection failures**: Verify connection configuration
4. **Resource conflicts**: Use appropriate task pools

### Debugging
```bash
# Test DAG syntax
python /path/to/dag_file.py

# List DAGs
docker exec shared-airflow-webserver airflow dags list

# Test specific task
docker exec shared-airflow-webserver airflow tasks test dag_id task_id 2024-01-01
``` 