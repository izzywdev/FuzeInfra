import fileinput

# Path to the swagger_server.py file
file_path = 'src/swagger_server.py'

# Define the new Api initialization line
new_api_line = """
api = Api(
    app,
    version='1.0.0',
    title='Mendys Robot Scraper API',
    description='''
    **Comprehensive API for Mendys Robot Scraper Platform**
    
    This API provides endpoints for:
    - 🤖 **Robot Scraping**: FANUC, KUKA, Universal Robots, ABB
    - 🔗 **WordPress Integration**: WooCommerce product sync
    - 🏪 **WooCommerce Discovery**: Brands and taxonomy discovery
    - 🔍 **Quality Assurance**: AI-powered product analysis
    - ⚙️ **Settings Management**: Platform configuration
    - 🕸️ **Site Discovery**: AI-powered robotics site discovery
    - 📊 **Monitoring**: Health checks and metrics
    
    **Target Platform**: SmartHubShopper.com
    ''',
    doc='/api/docs/',
    prefix='/api',
    authorizations=authorizations,
    contact='Mendys Development Team',
    contact_email='dev@mendys.com'
)
"""

# Read the file and replace the Api initialization
with fileinput.FileInput(file_path, inplace=True, backup='.bak', encoding='utf-8') as file:
    for line in file:
        if line.strip().startswith('api = Api('):
            # Skip the current Api initialization
            while not line.strip().endswith(')'):
                line = next(file)
            # Write the new Api initialization
            print(new_api_line, end='')
        else:
            # Write the original line
            print(line, end='') 