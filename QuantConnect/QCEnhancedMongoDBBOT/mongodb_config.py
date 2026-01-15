# MongoDB Configuration
# Only edit these values, don't set parameters in main.py

class MongoDBConfig:
    @staticmethod
    def get_config():
        return {
            'enabled': True,
            'api_url': 'https://mongodb-api-server.onrender.com/api',
            'api_key': 'adplxxry',  # Replace with your Railway API_KEY environment variable
            'database': 'quantconnect'
        }
