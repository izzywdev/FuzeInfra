-- Create robotics database and user
CREATE DATABASE robotics_db;
CREATE USER robotics WITH PASSWORD 'robotics';
GRANT ALL PRIVILEGES ON DATABASE robotics_db TO robotics;

-- Create wordpress cache database
CREATE DATABASE wordpress_cache;
GRANT ALL PRIVILEGES ON DATABASE wordpress_cache TO robotics;

-- Switch to robotics database
\c robotics_db;

-- Create tables for robotics data
CREATE TABLE IF NOT EXISTS robotics_sites (
    id SERIAL PRIMARY KEY,
    url VARCHAR(255) UNIQUE NOT NULL,
    name VARCHAR(255),
    description TEXT,
    confidence_score FLOAT,
    last_crawled TIMESTAMP,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS robot_products (
    id SERIAL PRIMARY KEY,
    site_id INTEGER REFERENCES robotics_sites(id),
    name VARCHAR(255) NOT NULL,
    model VARCHAR(255),
    manufacturer VARCHAR(255),
    product_url VARCHAR(500),
    description TEXT,
    specifications JSONB,
    images JSONB,
    price DECIMAL(10,2),
    currency VARCHAR(3) DEFAULT 'USD',
    payload_kg FLOAT,
    reach_mm FLOAT,
    axes INTEGER,
    controller VARCHAR(255),
    applications JSONB,
    confidence_score FLOAT,
    wordpress_product_id INTEGER,
    last_synced TIMESTAMP,
    status VARCHAR(50) DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crawl_sessions (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) UNIQUE NOT NULL,
    sites_crawled INTEGER DEFAULT 0,
    products_found INTEGER DEFAULT 0,
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP,
    status VARCHAR(50) DEFAULT 'running',
    error_message TEXT,
    metadata JSONB
);

CREATE TABLE IF NOT EXISTS sync_logs (
    id SERIAL PRIMARY KEY,
    product_id INTEGER REFERENCES robot_products(id),
    action VARCHAR(50) NOT NULL, -- 'create', 'update', 'skip'
    wordpress_product_id INTEGER,
    success BOOLEAN DEFAULT FALSE,
    error_message TEXT,
    sync_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for better performance
CREATE INDEX idx_robotics_sites_url ON robotics_sites(url);
CREATE INDEX idx_robot_products_site_id ON robot_products(site_id);
CREATE INDEX idx_robot_products_model ON robot_products(model);
CREATE INDEX idx_robot_products_manufacturer ON robot_products(manufacturer);
CREATE INDEX idx_robot_products_wordpress_id ON robot_products(wordpress_product_id);
CREATE INDEX idx_crawl_sessions_session_id ON crawl_sessions(session_id);
CREATE INDEX idx_sync_logs_product_id ON sync_logs(product_id);

-- Grant permissions to robotics user
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO robotics;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO robotics; 