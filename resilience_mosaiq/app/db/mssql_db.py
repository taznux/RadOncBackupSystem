import os
import logging
import sys
from urllib.parse import quote
from sqlalchemy import create_engine

# Set up MosaiqDB
server_mssql = os.getenv("MSSQL_IP")
user_mssql = os.getenv("MSSQL_ID")
password_mssql = quote(os.getenv("MSSQL_PW"))

mssql_connect_uri = f"mssql+pymssql://{user_mssql}:{password_mssql}@{server_mssql}/mosaiq"

def connect_mssql():
    if not hasattr(connect_mssql, "engine"):
        connect_mssql.engine = create_engine(mssql_connect_uri)
        #log_info(sys._getframe().f_code.co_name, f"Connected to MSSQL {connect_mssql.engine}")
        logging.log(logging.INFO,f"{sys._getframe().f_code.co_name} Connected to MSSQL {connect_mssql.engine}")
        
    return connect_mssql.engine