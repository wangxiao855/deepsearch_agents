"""
口碑评测子智能体，负责专业媒体和实测怎么看这款车。
"""
from agent.load_prompt import sub_agents_config
from tools.internet_search_tool import internet_search_tool
from tools.webpage_tool import fetch_webpage

review_sub_agent = {
    "name": sub_agents_config["review"]["name"],
    "description": sub_agents_config["review"]["description"],
    "system_prompt": sub_agents_config["review"]["system_prompt"],
    "tools": [internet_search_tool, fetch_webpage],
}
