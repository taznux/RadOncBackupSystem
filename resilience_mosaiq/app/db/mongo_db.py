import os
import logging
import sys
from pymongo import MongoClient
from urllib.parse import quote
from bson.codec_options import CodecOptions
import pytz


# Set up MongoDB as the archive and user profile
def connect_mongodb(database_name, collection_name, timezone=None):

    # Set up MongoDB as the archive and user profile
    server_mongo = os.getenv("MONGODB_IP")
    user_mongo = os.getenv("MONGODB_ID")
    password_mongo = quote(os.getenv("MONGODB_PW"))

    mongodb_connect_uri = f"mongodb://{user_mongo}:{password_mongo}@{server_mongo}:27017"

    if not hasattr(connect_mongodb, "mongo_client"):
        connect_mongodb.mongo_client = MongoClient(mongodb_connect_uri, tz_aware=True)
        #log_info(sys._getframe().f_code.co_name, f"Connected to MongoDB {connect_mongodb.mongo_client}")
        logging.log(logging.INFO,f"{sys._getframe().f_code.co_name} Connected to MongoDB {connect_mongodb.mongo_client}")
    
    database = connect_mongodb.mongo_client[database_name]
    collection = database[collection_name]
    
    if timezone is not None:
        collection = collection.with_options(
            codec_options=CodecOptions(tz_aware=True, tzinfo=timezone)
        )

    return collection
