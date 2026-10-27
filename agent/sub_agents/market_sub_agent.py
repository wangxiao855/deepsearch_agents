"""
行情情报子智能体，负责行情调查
"""
from agent.load_prompt import sub_agents_config
from tools.car_db_tool import get_monthly_sales, get_trim_price, search_model_library
from tools.internet_search_tool import internet_search_tool
from tools.webpage_tool import fetch_webpage

market_sub_agent = {
    "name": sub_agents_config["market"]["name"],
    "description": sub_agents_config["market"]["description"],
    "system_prompt": sub_agents_config["market"]["system_prompt"],
    "tools": [
        search_model_library,
        get_trim_price,
        get_monthly_sales,
        internet_search_tool,
        fetch_webpage,
    ],
}
