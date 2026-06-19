#!/usr/bin/env node
/**
 * Node.js Client Example for FuzeInfra Service Discovery
 * Demonstrates how to integrate Consul service discovery in Node.js applications
 */

const consul = require('consul');

class FuzeInfraServiceDiscovery {
    constructor(options = {}) {
        this.consul = consul({
            host: options.host || 'localhost',
            port: options.port || 8500,
            secure: options.secure || false,
            promisify: true
        });
        this.domainSuffix = options.domainSuffix || 'dev.local';
    }

    /**
     * Register this service with Consul
     */
    async registerService(serviceName, port, options = {}) {
        try {
            const serviceConfig = {
                name: serviceName,
                id: `${serviceName}-${port}`,
                address: options.address || serviceName,
                port: port,
                tags: options.tags || [],
                meta: {
                    'registered_at': new Date().toISOString(),
                    'registered_by': 'fuzeinfra-nodejs-client',
                    'domain': `${serviceName}.${this.domainSuffix}`,
                    ...options.meta
                }
            };

            // Add health check if provided
            if (options.healthCheck) {
                serviceConfig.check = {
                    http: `http://${serviceConfig.address}:${port}${options.healthCheck}`,
                    interval: options.healthInterval || '10s',
                    timeout: options.healthTimeout || '5s'
                };
            }

            await this.consul.agent.service.register(serviceConfig);
            
            console.log(`✅ Service registered: ${serviceName} at ${serviceConfig.address}:${port}`);
            
            return {
                success: true,
                serviceId: serviceConfig.id,
                serviceName: serviceName,
                address: serviceConfig.address,
                port: port,
                domain: `${serviceName}.${this.domainSuffix}`
            };
        } catch (error) {
            console.error(`❌ Failed to register service ${serviceName}:`, error.message);
            return { success: false, error: error.message };
        }
    }

    /**
     * Discover a service by name
     */
    async discoverService(serviceName, healthyOnly = true) {
        try {
            const services = await this.consul.health.service({
                service: serviceName,
                passing: healthyOnly
            });

            if (!services || services.length === 0) {
                return {
                    success: false,
                    error: `No ${healthyOnly ? 'healthy ' : ''}services found for ${serviceName}`
                };
            }

            const instances = services.map(serviceData => {
                const service = serviceData.Service;
                const checks = serviceData.Checks || [];
                
                // Determine health status
                const healthStatus = checks.every(check => check.Status === 'passing') ? 'passing' : 'failing';
                
                return {
                    serviceId: service.ID,
                    serviceName: service.Service,
                    address: service.Address,
                    port: service.Port,
                    url: `http://${service.Address}:${service.Port}`,
                    domain: `${service.Service}.${this.domainSuffix}`,
                    domainUrl: `http://${service.Service}.${this.domainSuffix}`,
                    tags: service.Tags || [],
                    meta: service.Meta || {},
                    healthStatus: healthStatus,
                    healthChecks: checks.length
                };
            });

            return {
                success: true,
                serviceName: serviceName,
                instances: instances,
                totalInstances: instances.length,
                primaryInstance: instances[0]
            };
        } catch (error) {
            console.error(`❌ Failed to discover service ${serviceName}:`, error.message);
            return { success: false, error: error.message };
        }
    }

    /**
     * Get service URL (convenience method)
     */
    async getServiceUrl(serviceName, useDocker = true) {
        const discovery = await this.discoverService(serviceName);
        
        if (!discovery.success || !discovery.primaryInstance) {
            throw new Error(`Service ${serviceName} not found or unhealthy`);
        }

        const instance = discovery.primaryInstance;
        
        // Return Docker network URL (for container-to-container communication)
        // or domain URL (for local development)
        return useDocker ? instance.url : instance.domainUrl;
    }

    /**
     * Deregister service on process exit
     */
    async deregisterService(serviceName, port) {
        try {
            const serviceId = `${serviceName}-${port}`;
            await this.consul.agent.service.deregister(serviceId);
            console.log(`✅ Service deregistered: ${serviceId}`);
        } catch (error) {
            console.error(`❌ Failed to deregister service:`, error.message);
        }
    }

    /**
     * Setup automatic service deregistration on process exit
     */
    setupGracefulShutdown(serviceName, port) {
        const cleanup = async () => {
            console.log('\n🔄 Shutting down gracefully...');
            await this.deregisterService(serviceName, port);
            process.exit(0);
        };

        process.on('SIGINT', cleanup);
        process.on('SIGTERM', cleanup);
        process.on('SIGQUIT', cleanup);
    }
}

// Example usage
async function example() {
    const serviceDiscovery = new FuzeInfraServiceDiscovery();

    // Example 1: Register this service
    const serviceName = 'example-service';
    const port = 3000;
    
    const registration = await serviceDiscovery.registerService(serviceName, port, {
        healthCheck: '/health',
        tags: ['api', 'example'],
        meta: { version: '1.0.0' }
    });

    if (registration.success) {
        console.log('Service registration successful:', registration);
        
        // Setup graceful shutdown
        serviceDiscovery.setupGracefulShutdown(serviceName, port);
    }

    // Example 2: Discover another service
    try {
        const fuzeagentUrl = await serviceDiscovery.getServiceUrl('fuzeagent');
        console.log(`🔍 fuzeagent URL: ${fuzeagentUrl}`);
        
        // Use the service URL for API calls
        // const response = await fetch(`${fuzeagentUrl}/api/status`);
    } catch (error) {
        console.log(`⚠️  fuzeagent not available: ${error.message}`);
    }

    // Example 3: Discover with detailed information
    const discovery = await serviceDiscovery.discoverService('fuzeagent');
    if (discovery.success) {
        console.log('🔍 fuzeagent discovery result:', discovery);
        
        // Access specific instance information
        const instance = discovery.primaryInstance;
        console.log(`Primary instance: ${instance.address}:${instance.port}`);
        console.log(`Health status: ${instance.healthStatus}`);
        console.log(`Tags: ${instance.tags.join(', ')}`);
    }
}

// Export for use as a module
module.exports = FuzeInfraServiceDiscovery;

// Run example if called directly
if (require.main === module) {
    example().catch(console.error);
}