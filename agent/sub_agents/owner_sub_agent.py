"""
车主反馈子智能体，负责车主的看法。
"""
from agent.load_prompt import sub_agents_config
from tools.car_db_tool import (
    get_complaint_cases,
    get_complaint_summary,
    search_car_series,
)
from tools.internet_search_tool import internet_search_tool
from tools.webpage_tool import fetch_webpage

owner_sub_agent = {
    "name": sub_agents_config["owner"]["name"],
    "description": sub_agents_config["owner"]["description"],
    "system_prompt": sub_agents_config["owner"]["system_prompt"],
    "tools": [
        search_car_series,
        get_complaint_cases,
        get_complaint_summary,
        internet_search_tool,
        fetch_webpage,
    ],
}
